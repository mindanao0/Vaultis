# -*- coding: utf-8 -*-
"""PDF export utilities for monthly portfolio report."""

from __future__ import annotations

from io import BytesIO
import re

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from analysis.ai_advisor import get_monthly_advice
from analysis.risk import calculate_risk_metrics
from analysis.returns import calculate_period_returns
from data.fetcher import fetch_adjusted_close_data
from portfolio.tracker import get_portfolio_summary, get_total_summary
from utils.config import get_tickers


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_advice_items(advice_text: str) -> tuple[list[str], list[str], list[str]]:
    analysis_lines: list[str] = []
    allocation_lines: list[str] = []
    risk_lines: list[str] = []

    for line in advice_text.splitlines():
        text = line.strip()
        if not text:
            continue
        if "— RSI" in text or "RSI" in text:
            analysis_lines.append(text)
            continue
        if re.search(r"\(\d+(\.\d+)?%\)", text):
            allocation_lines.append(text)
            continue
        if text.startswith("⚠️") or "ความเสี่ยง" in text:
            risk_lines.append(text)
            continue
        if len(risk_lines) > 0 and not text.startswith(("📅", "💰", "📊", "🤖")):
            risk_lines.append(text)

    return analysis_lines[:8], allocation_lines[:8], risk_lines[:5]


def _build_table(table_data: list[list[object]], col_widths: list[float] | None = None) -> Table:
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def generate_monthly_report(month: str, budget_thb: float) -> bytes:
    """Generate monthly portfolio PDF report and return file bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    elements: list[object] = []

    # Common data
    holdings_df = get_portfolio_summary()
    total_summary = get_total_summary()
    tickers = get_tickers()
    prices = fetch_adjusted_close_data(tickers=tickers, years=10)
    returns_df = calculate_period_returns(prices) if not prices.empty else pd.DataFrame()
    risk_df = calculate_risk_metrics(prices) if not prices.empty else pd.DataFrame()

    # Page 1: Portfolio summary
    elements.append(Paragraph(f"Vaultis Monthly Report - {month}", styles["Title"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Page 1 - Portfolio Summary", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * cm))

    summary_table_data = [
        ["Metric", "Value (THB)"],
        ["Total Invested", f"{_safe_float(total_summary.get('total_invested_thb')):,.2f}"],
        ["Current Value", f"{_safe_float(total_summary.get('current_value_thb')):,.2f}"],
        ["Profit / Loss", f"{_safe_float(total_summary.get('total_pnl_thb')):,.2f}"],
        ["Total Return (%)", f"{_safe_float(total_summary.get('total_return_pct')):,.2f}%"],
    ]
    elements.append(_build_table(summary_table_data, col_widths=[8 * cm, 8 * cm]))
    elements.append(Spacer(1, 0.4 * cm))

    elements.append(Paragraph("Holdings", styles["Heading3"]))
    if holdings_df.empty:
        elements.append(Paragraph("No portfolio transactions found.", styles["BodyText"]))
    else:
        holdings_cols = ["Ticker", "Shares", "Avg Cost (USD)", "Current Price (USD)", "P&L (THB)", "Return (%)"]
        holdings_table_data: list[list[object]] = [holdings_cols]
        for _, row in holdings_df[holdings_cols].iterrows():
            holdings_table_data.append(
                [
                    str(row["Ticker"]),
                    f"{_safe_float(row['Shares']):,.4f}",
                    f"{_safe_float(row['Avg Cost (USD)']):,.2f}",
                    f"{_safe_float(row['Current Price (USD)']):,.2f}",
                    f"{_safe_float(row['P&L (THB)']):,.2f}",
                    f"{_safe_float(row['Return (%)']):,.2f}%",
                ]
            )
        elements.append(_build_table(holdings_table_data, col_widths=[2.2 * cm, 2.5 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm, 2.3 * cm]))

    # Page 2: Performance
    elements.append(PageBreak())
    elements.append(Paragraph(f"Vaultis Monthly Report - {month}", styles["Title"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Page 2 - Performance", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * cm))

    elements.append(Paragraph("Return Analysis (1M / 3M / 6M / 1Y)", styles["Heading3"]))
    if returns_df.empty:
        elements.append(Paragraph("No return data available.", styles["BodyText"]))
    else:
        return_periods = [period for period in ["1M", "3M", "6M", "1Y"] if period in returns_df.index]
        return_table_data: list[list[object]] = [["Period"] + list(returns_df.columns)]
        for period in return_periods:
            row = [period]
            for ticker in returns_df.columns:
                row.append(f"{_safe_float(returns_df.loc[period, ticker]):,.2f}%")
            return_table_data.append(row)
        elements.append(_build_table(return_table_data))

    elements.append(Spacer(1, 0.4 * cm))
    elements.append(Paragraph("Risk Metrics (Volatility / Sharpe / Drawdown)", styles["Heading3"]))
    if risk_df.empty:
        elements.append(Paragraph("No risk metrics data available.", styles["BodyText"]))
    else:
        risk_table_data: list[list[object]] = [["Ticker", "Volatility", "Sharpe", "Drawdown"]]
        for ticker in risk_df.index:
            risk_table_data.append(
                [
                    str(ticker),
                    f"{_safe_float(risk_df.loc[ticker, 'Volatility']):,.4f}",
                    f"{_safe_float(risk_df.loc[ticker, 'Sharpe Ratio']):,.4f}",
                    f"{_safe_float(risk_df.loc[ticker, 'Max Drawdown']):,.4f}",
                ]
            )
        elements.append(_build_table(risk_table_data, col_widths=[3 * cm, 4 * cm, 4 * cm, 4 * cm]))

    # Page 3: AI Advisor summary
    elements.append(PageBreak())
    elements.append(Paragraph(f"Vaultis Monthly Report - {month}", styles["Title"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Page 3 - AI Advisor Summary", styles["Heading2"]))
    elements.append(Spacer(1, 0.2 * cm))

    try:
        advice = get_monthly_advice(budget_thb=budget_thb, send_discord=False)
        advice_text = str(advice.get("advice_text", "")).strip()
    except Exception as exc:
        advice_text = f"AI analysis unavailable: {exc}"

    analysis_lines, allocation_lines, risk_lines = _extract_advice_items(advice_text)
    elements.append(Paragraph("Monthly AI Analysis", styles["Heading3"]))
    if analysis_lines:
        for line in analysis_lines:
            elements.append(Paragraph(f"- {line}", styles["BodyText"]))
    else:
        elements.append(Paragraph(advice_text[:1200] or "No AI analysis content.", styles["BodyText"]))

    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Recommended Allocation", styles["Heading3"]))
    if allocation_lines:
        for line in allocation_lines:
            elements.append(Paragraph(f"- {line}", styles["BodyText"]))
    else:
        elements.append(Paragraph("No allocation details found in this run.", styles["BodyText"]))

    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph("Key Risks To Monitor", styles["Heading3"]))
    if risk_lines:
        for line in risk_lines:
            elements.append(Paragraph(f"- {line}", styles["BodyText"]))
    else:
        elements.append(Paragraph("No specific risk section found in AI output.", styles["BodyText"]))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
