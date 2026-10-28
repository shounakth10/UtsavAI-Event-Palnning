"""PDF, iCalendar, and WhatsApp exports for event plans."""

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
from xml.sax.saxutils import escape

from ics import Calendar, Event
from ics.alarm import DisplayAlarm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

from schemas import EventPlan
from services.budget import format_inr

_SAFFRON = colors.HexColor("#b9470c")
_LIGHT_SAFFRON = colors.HexColor("#fff1e8")
_TEXT = colors.HexColor("#25211e")


def _register_pdf_fonts(plan: EventPlan) -> tuple[str, str]:
    devanagari = re.search(
        r"[\u0900-\u097f]",
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False),
    ) is not None
    if not devanagari:
        return "Helvetica", "Helvetica-Bold"

    noto_regular = Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf")
    noto_bold = Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf")
    windows_collection = Path("C:/Windows/Fonts/Nirmala.ttc")
    if noto_regular.is_file():
        regular_font = TTFont("UtsavDevanagari", str(noto_regular), shapable=True)
        bold_font = TTFont(
            "UtsavDevanagari-Bold",
            str(noto_bold if noto_bold.is_file() else noto_regular),
            shapable=True,
        )
    elif windows_collection.is_file():
        regular_font = TTFont(
            "UtsavDevanagari",
            str(windows_collection),
            subfontIndex=0,
            shapable=True,
        )
        bold_font = TTFont(
            "UtsavDevanagari-Bold",
            str(windows_collection),
            subfontIndex=1,
            shapable=True,
        )
    else:
        raise RuntimeError(
            "A Devanagari font is required for Hindi/Marathi PDF export. "
            "Install Noto Sans Devanagari or Nirmala UI."
        )

    pdfmetrics.registerFont(regular_font)
    pdfmetrics.registerFont(bold_font)
    pdfmetrics.registerFontFamily(
        "UtsavDevanagari",
        normal=regular_font.fontName,
        bold=bold_font.fontName,
        italic=regular_font.fontName,
        boldItalic=bold_font.fontName,
    )
    return regular_font.fontName, bold_font.fontName


def _paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)


def _pdf_inr(amount: int) -> str:
    return format_inr(amount).replace("₹", "INR ")


def generate_pdf(plan: EventPlan) -> bytes:
    """Render a readable A4 plan with budget, timeline, vendors, and risks."""
    regular_font, bold_font = _register_pdf_fonts(plan)
    shaping = regular_font.startswith("UtsavDevanagari")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "UtsavTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=20,
        leading=25,
        textColor=_SAFFRON,
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
        shaping=shaping,
    )
    heading_style = ParagraphStyle(
        "UtsavHeading",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=13,
        leading=17,
        textColor=_SAFFRON,
        spaceBefore=5 * mm,
        spaceAfter=2 * mm,
        shaping=shaping,
    )
    body_style = ParagraphStyle(
        "UtsavBody",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=8.5,
        leading=12,
        textColor=_TEXT,
        shaping=shaping,
    )
    small_style = ParagraphStyle(
        "UtsavSmall",
        parent=body_style,
        fontSize=7.5,
        leading=10,
    )
    story = [
        _paragraph(plan.event.title, title_style),
        _paragraph(
            f"{plan.event.type.title()} · {plan.event.date} · {plan.event.city} · "
            f"{plan.event.guest_count} guests · {_pdf_inr(plan.event.total_budget_inr)}",
            body_style,
        ),
        _paragraph(
            f"Tradition: {plan.event.cultural_style} · Theme: {plan.event.theme}",
            body_style,
        ),
        Spacer(1, 3 * mm),
        _paragraph(plan.event.summary, body_style),
    ]

    def add_table(title: str, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
        story.append(_paragraph(title, heading_style))
        data = [[_paragraph(value, small_style) for value in headers]]
        data.extend(
            [[_paragraph(value, small_style) for value in row] for row in rows]
        )
        table = LongTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _LIGHT_SAFFRON),
            ("TEXTCOLOR", (0, 0), (-1, 0), _SAFFRON),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#ead8ca")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)

    add_table(
        "Budget",
        ["Category", "Allocation", "Amount", "Rationale"],
        [[item.category, f"{item.percentage:.2f}%", _pdf_inr(item.amount_inr), item.rationale]
         for item in plan.budget],
        [34 * mm, 19 * mm, 24 * mm, 103 * mm],
    )
    add_table(
        "Timeline",
        ["Due date", "Task", "Category", "Owner", "Depends on"],
        [[task.due_date, task.task, task.category, task.owner, ", ".join(task.depends_on) or "—"]
         for task in plan.timeline],
        [22 * mm, 63 * mm, 28 * mm, 27 * mm, 40 * mm],
    )

    add_table(
        "Ideas",
        ["Type", "Idea", "Tier", "Estimated cost", "Execution"],
        [[
            idea.type,
            idea.title,
            idea.tier,
            _pdf_inr(idea.estimated_cost_inr),
            idea.description,
        ] for idea in plan.ideas],
        [20 * mm, 35 * mm, 20 * mm, 28 * mm, 77 * mm],
    )

    story.append(_paragraph("Vendor checklist", heading_style))
    for vendor in plan.vendors:
        story.append(KeepTogether([
            _paragraph(f"<b>{escape(vendor.category)}</b> — {escape(vendor.what_to_book)}", body_style),
            _paragraph(
                f"Scope: {escape(vendor.quantity_or_scope)} · Estimate: "
                f"{_pdf_inr(vendor.estimated_cost_inr)} · Book by: {escape(vendor.book_by_date)}",
                small_style,
            ),
            _paragraph(
                "Questions: " + "; ".join(escape(value) for value in vendor.questions_to_ask),
                small_style,
            ),
            _paragraph(
                "Red flags: " + "; ".join(escape(value) for value in vendor.red_flags),
                small_style,
            ),
            _paragraph(f"Negotiation tip: {escape(vendor.negotiation_tip)}", small_style),
            Spacer(1, 2 * mm),
        ]))

    for title, items in (("Risks", plan.risks), ("Assumptions", plan.assumptions)):
        story.append(_paragraph(title, heading_style))
        story.extend(_paragraph(f"• {item}", body_style) for item in items)

    from io import BytesIO

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=plan.event.title,
        author="UtsavAI",
    )
    document.build(story)
    return output.getvalue()


def generate_ics(plan: EventPlan) -> str:
    """Generate an ICS calendar with a two-day alarm for every timeline task."""
    calendar = Calendar()
    calendar.creator = "UtsavAI"
    for task in plan.timeline:
        due_date = date.fromisoformat(task.due_date)
        event = Event()
        event.name = f"[{task.category}] {task.task}"
        event.begin = datetime.combine(due_date, time.min, tzinfo=timezone.utc)
        event.description = (
            f"UtsavAI milestone: {task.task}\n"
            f"Category: {task.category}\n"
            f"Owner: {task.owner}\n"
            f"Days before event: {task.days_before_event}"
        )
        event.alarms.append(
            DisplayAlarm(
                trigger=timedelta(days=-2),
                display_text=f"UtsavAI reminder: {task.task}",
            )
        )
        calendar.events.add(event)
    return calendar.serialize()


def generate_whatsapp_summary(plan: EventPlan) -> str:
    """Generate a short WhatsApp-ready event summary and key milestones."""
    event = plan.event
    lines = [
        f"🎉 *{event.title}*",
        f"📅 {event.date} · 📍 {event.city} · 👥 {event.guest_count} guests",
        f"💰 Budget: {format_inr(event.total_budget_inr)} · 🎨 {event.theme}",
        "",
        event.summary,
        "",
        "*Key milestones*",
    ]
    lines.extend(
        f"• {task.due_date}: {task.task}"
        for task in plan.timeline[:4]
    )
    if plan.risks:
        lines.extend(["", f"⚠️ {plan.risks[0]}"])
    lines.append("\n_Planned with UtsavAI. Vendor costs are estimates._")
    return "\n".join(lines)
