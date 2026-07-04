# TradingAgents/graph/trading_graph.py

import logging
import os
from pathlib import Path
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)

from langgraph.prebuilt import ToolNode

from tradingagents.llm_clients import create_llm_client

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    resolve_instrument_identity,
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news
)

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
        deep_callbacks: Optional[List] = None,
        quick_callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Callback handlers applied to both LLMs
            deep_callbacks: Callback handlers applied only to the deep-think LLM
            quick_callbacks: Callback handlers applied only to the quick-think LLM
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration.
        # deep_think_provider overrides llm_provider for Research Manager and
        # Portfolio Manager only; all other agents use llm_provider (quick tier).
        quick_provider = self.config["llm_provider"]
        deep_provider = self.config.get("deep_think_provider") or quick_provider

        deep_cb = (deep_callbacks or []) + self.callbacks
        quick_cb = (quick_callbacks or []) + self.callbacks

        deep_kwargs = {
            **self._get_provider_kwargs(deep_provider),
            **({"callbacks": deep_cb} if deep_cb else {}),
        }
        quick_kwargs = {
            **self._get_provider_kwargs(quick_provider),
            **({"callbacks": quick_cb} if quick_cb else {}),
        }

        # Analyst tier: same model as quick but with low temperature + optional seed
        # to reduce Phase I run-to-run variance without dampening debate creativity.
        analyst_kwargs = dict(quick_kwargs)
        analyst_temperature = self.config.get("analyst_temperature")
        if analyst_temperature is not None and analyst_temperature != "":
            analyst_kwargs["temperature"] = float(analyst_temperature)
        analyst_seed = self.config.get("analyst_seed")
        if analyst_seed is not None and analyst_seed != "":
            analyst_kwargs["seed"] = int(analyst_seed)

        # PM tier: deep model with analyst_temperature to stabilise the final decision.
        # Seed is not forwarded to Anthropic (unsupported).
        if analyst_temperature is not None and analyst_temperature != "":
            deep_kwargs["temperature"] = float(analyst_temperature)

        deep_client = create_llm_client(
            provider=deep_provider,
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **deep_kwargs,
        )
        quick_client = create_llm_client(
            provider=quick_provider,
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **quick_kwargs,
        )
        analyst_client = create_llm_client(
            provider=quick_provider,
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **analyst_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()
        self.analyst_thinking_llm = analyst_client.get_llm()
        
        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            analyst_concurrency_limit=self.config.get("analyst_concurrency_limit", 1),
            analyst_thinking_llm=self.analyst_thinking_llm,
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self, provider: str = None) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation.

        Args:
            provider: Provider name to build kwargs for. Defaults to llm_provider.
        """
        kwargs = {}
        provider = (provider or self.config.get("llm_provider", "")).lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        # Sampling temperature is cross-provider: forward it whenever set.
        # float() here so a value coming from a TRADINGAGENTS_TEMPERATURE env
        # string ("0.2") works the same as a programmatic float.
        temperature = self.config.get("temperature")
        if temperature is not None and temperature != "":
            kwargs["temperature"] = float(temperature)

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). US-listed tickers without a dotted suffix fall through to the
        empty-suffix entry (SPY by default). Unrecognised suffixes (including
        US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
        entry, which is the right default because the alpha calculation works
        in USD.
        """
        explicit = self.config.get("benchmark_ticker")
        if explicit:
            return explicit
        # VN tickers (HOSE/HNX) benchmark against VNINDEX, not SPY.
        from tradingagents.dataflows.market_router import is_vn_ticker
        if is_vn_ticker(ticker):
            return "VNINDEX"
        benchmark_map = self.config.get("benchmark_map", {})
        ticker_upper = ticker.upper()
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5,
        benchmark: str = "SPY",
    ) -> Tuple[Optional[float], Optional[float], Optional[int]]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        ``benchmark`` is the index used as the alpha baseline (resolved by the
        caller via ``_resolve_benchmark``). Returns ``(raw_return, alpha_return,
        actual_holding_days)`` or ``(None, None, None)`` if price data is
        unavailable (too recent, delisted, or network error).
        """
        from tradingagents.dataflows.market_router import is_vn_ticker
        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            if is_vn_ticker(ticker):
                # VN: lấy giá qua vnstock_data (yfinance không có mã HOSE/HNX),
                # benchmark VNINDEX. Trả về None nếu chưa đủ dữ liệu (ngày tương lai).
                from tradingagents.agents.utils.vn_technical_fetcher import _safe_hist
                sdf = _safe_hist(ticker, trade_date, end_str)
                bdf = _safe_hist(benchmark or "VNINDEX", trade_date, end_str)
                if len(sdf) < 2 or len(bdf) < 2:
                    return None, None, None
                sc, bc = sdf["close"], bdf["close"]
                actual_days = min(holding_days, len(sc) - 1, len(bc) - 1)
                raw = float((sc.iloc[actual_days] - sc.iloc[0]) / sc.iloc[0])
                bench_ret = float((bc.iloc[actual_days] - bc.iloc[0]) / bc.iloc[0])
                return raw, raw - bench_ret, actual_days

            stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
            bench = yf.Ticker(benchmark).history(start=trade_date, end=end_str)

            if len(stock) < 2 or len(bench) < 2:
                return None, None, None

            actual_days = min(holding_days, len(stock) - 1, len(bench) - 1)
            raw = float(
                (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
                / stock["Close"].iloc[0]
            )
            bench_ret = float(
                (bench["Close"].iloc[actual_days] - bench["Close"].iloc[0])
                / bench["Close"].iloc[0]
            )
            alpha = raw - bench_ret
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        benchmark = self._resolve_benchmark(ticker)
        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(
                ticker, entry["date"], benchmark=benchmark,
            )
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
                benchmark_name=benchmark,
            )
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def resolve_instrument_context(self, ticker: str, asset_type: str = "stock") -> str:
        """Resolve ticker identity once and return the full instrument context.

        Deterministic yfinance lookup (cached, fail-open) injected into a
        context string so every agent anchors to the real company instead of
        hallucinating one from the price chart (#814). Both the propagate()
        path and the CLI call this so the resolved identity reaches the whole
        graph regardless of entry point.
        """
        identity = resolve_instrument_identity(ticker)
        return build_instrument_context(ticker, asset_type, identity)

    def _resolve_company_profile(self, ticker: str, asset_type: str = "stock") -> str:
        """Fetch company profile once at run start for C5 entity grounding.

        Injected into fundamentals_analyst (prevents hallucination) and reused by
        the C3 fact-check gate. Returns "" for non-VN tickers or on failure.
        """
        from tradingagents.dataflows.market_router import is_vn_ticker
        if asset_type != "stock" or not is_vn_ticker(ticker):
            return ""
        try:
            from tradingagents.agents.utils.vn_entity_verifier import fetch_company_profile_block
            profile = fetch_company_profile_block(ticker)
            if profile:
                logger.info("Company profile fetched for %s (%d chars)", ticker, len(profile))
            else:
                logger.warning("Empty company profile for %s — C5 grounding disabled", ticker)
            return profile
        except Exception as e:
            logger.warning("Could not fetch company profile for %s: %s", ticker, e)
            return ""

    def _resolve_financials(self, ticker: str, asset_type: str = "stock", trade_date: str | None = None,
                            beta: float | None = None) -> Tuple[str, str]:
        """Compute the canonical financials ONCE at run start (A1 single source of truth).

        Python-computed payload (A2/A3) injected into every number-touching
        agent. Best-effort: returns ("", "") for non-VN tickers or on failure,
        so agents degrade to ticker-only context instead of fabricating numbers.

        ``beta`` (Task 10 R1, computed once from price history by
        ``_resolve_risk_metrics``) feeds the COE calc in valuation_engine (V1)
        so justified P/B / DDM use the ticker's real beta instead of the
        default_beta=1.0 fallback.
        """
        from tradingagents.dataflows.market_router import is_vn_ticker
        if asset_type != "stock" or not is_vn_ticker(ticker):
            return "", ""
        try:
            from tradingagents.agents.utils.vn_financial_fetcher import build_financials_payload
            payload = build_financials_payload(ticker, trade_date=trade_date, beta=beta)
            if payload.get("error"):
                logger.warning("Financials payload for %s failed: %s", ticker, payload["error"])
                return "", ""
            return payload.get("block", ""), payload.get("chart_json", "")
        except Exception as e:
            logger.warning("Could not build financials payload for %s: %s", ticker, e)
            return "", ""

    def _resolve_risk_metrics(self, ticker: str, asset_type: str = "stock", trade_date: str | None = None) -> Tuple[str, "dict"]:
        """Compute deterministic risk metrics ONCE at run start (Task 10 R1).

        Beta/VaR/drawdown/ADTV/days-to-liquidate từ price history, inject vào 3
        risk debator. Best-effort: ("", {}) cho non-VN hoặc lỗi → debator degrade sạch.
        Trả cả ``data`` để beta có thể chảy sang valuation_engine (V1 COE).
        """
        from tradingagents.dataflows.market_router import is_vn_ticker
        if asset_type != "stock" or not is_vn_ticker(ticker):
            return "", {}
        try:
            from tradingagents.agents.utils.vn_risk_metrics import build_risk_metrics_block
            res = build_risk_metrics_block(ticker, trade_date=trade_date)
            if res.get("error"):
                logger.warning("Risk metrics for %s failed: %s", ticker, res["error"])
                return "", {}
            return res.get("block", ""), res.get("data", {})
        except Exception as e:
            logger.warning("Could not build risk metrics for %s: %s", ticker, e)
            return "", {}

    def propagate(self, company_name, trade_date, asset_type: str = "stock", run_type: str = "unknown"):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.
        """
        self.ticker = company_name

        # Task 4B: khoá ngày phân tích vào ContextVar để mọi news tool (do LLM gọi,
        # không thấy state) truy vấn tin trong [trade_date − N, trade_date], không
        # rò rỉ tin tương lai khi backtest. Reset trong finally.
        from tradingagents.dataflows.run_context import set_trade_date, reset_trade_date
        _td_token = set_trade_date(str(trade_date))

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        self._resolve_pending_entries(company_name)

        # Recompile with a checkpointer if the user opted in.
        if self.config.get("checkpoint_enabled"):
            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], company_name
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            step = checkpoint_step(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )
            if step is not None:
                logger.info(
                    "Resuming from step %d for %s on %s", step, company_name, trade_date
                )
            else:
                logger.info("Starting fresh for %s on %s", company_name, trade_date)

        try:
            return self._run_graph(company_name, trade_date, asset_type=asset_type, run_type=run_type)
        finally:
            reset_trade_date(_td_token)
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def _run_graph(self, company_name, trade_date, asset_type: str = "stock", run_type: str = "unknown"):
        """Execute the graph and write the resulting state to disk and memory log."""
        # Initialize state — inject memory log context for PM and the
        # deterministically resolved instrument identity for all agents.
        past_context = self.memory_log.get_past_context(company_name)
        instrument_context = self.resolve_instrument_context(company_name, asset_type)
        # Risk metrics trước để beta thật (R1) chảy vào COE của valuation_engine (V1),
        # thay vì default_beta=1.0 — cùng 1 lần fetch giá, không tính beta 2 lần.
        risk_metrics_block, risk_metrics_data = self._resolve_risk_metrics(company_name, asset_type, trade_date=str(trade_date))
        beta = risk_metrics_data.get("beta")
        financials_block, financials_chart_json = self._resolve_financials(
            company_name, asset_type, trade_date=str(trade_date), beta=beta
        )
        company_profile_block = self._resolve_company_profile(company_name, asset_type)
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
            financials_block=financials_block,
            financials_chart_json=financials_chart_json,
            company_profile_block=company_profile_block,
            risk_metrics_block=risk_metrics_block,
        )
        args = self.propagator.get_graph_args()

        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        if self.config.get("checkpoint_enabled"):
            tid = thread_id(company_name, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        if self.debug:
            trace = []
            for chunk in self.graph.stream(init_agent_state, **args):
                if len(chunk["messages"]) == 0:
                    pass
                else:
                    chunk["messages"][-1].pretty_print()
                    trace.append(chunk)
            # Streamed chunks are per-node deltas. Merge them so the returned
            # state matches what graph.invoke() yields in the non-debug path.
            final_state = {}
            for chunk in trace:
                final_state.update(chunk)
        else:
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection.
        self.curr_state = final_state

        # Log state to disk.
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        # Backtest runs do not write to trading_memory — calibration DB is the record.
        if run_type != "backtest":
            self.memory_log.store_decision(
                ticker=company_name,
                trade_date=trade_date,
                final_trade_decision=final_state["final_trade_decision"],
                run_type=run_type,
            )

        # Clear checkpoint on successful completion to avoid stale state.
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )

        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "market_analyst_rating": final_state.get("market_analyst_rating"),
            "news_analyst_rating": final_state.get("news_analyst_rating"),
            "fundamentals_analyst_rating": final_state.get("fundamentals_analyst_rating"),
            "market_analyst_reason": final_state.get("market_analyst_reason"),
            "news_analyst_reason": final_state.get("news_analyst_reason"),
            "fundamentals_analyst_reason": final_state.get("fundamentals_analyst_reason"),
            "rm_rating": final_state.get("rm_rating"),
            "rm_reason": final_state.get("rm_reason"),
            "trader_rating": final_state.get("trader_rating"),
            "trader_reason": final_state.get("trader_reason"),
            "pm_rating": final_state.get("pm_rating"),
            "pm_reason": final_state.get("pm_reason"),
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
