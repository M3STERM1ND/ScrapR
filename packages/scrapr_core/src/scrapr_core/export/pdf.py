"""The PDF renderer (`REQ-EXP-001`, `DEC-21`).

A pure function from an `ExportDocument` and a `Theme` to bytes. It decides how
things look and nothing about what they say.

**Fonts are embedded**, from the Noto files bundled beside this module, so the
document looks the same on every machine that opens it and a figure never
renders in a fallback face.

**Every string from the version is escaped before it reaches ReportLab's
paragraph markup.** That markup is a small XML dialect, and retrieved text is
untrusted (`REQ-SEC-013`): an excerpt containing `<link href=...>` must print
as those characters, not become a link in a file someone opens.

**Charts are drawn from the stored spec as vectors** (`DEC-11`): axes, points
and labels, with the first series in the theme's accent and later ones in its
neutral, distinguished by marker shape as well as colour.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Final
from xml.sax.saxutils import escape, quoteattr

from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from scrapr_core.db.enums import VizKind
from scrapr_core.export.document import (
    ExportChart,
    ExportClaim,
    ExportDocument,
    ExportReference,
    format_date,
    format_value,
)
from scrapr_core.export.themes import RULES, Cover, Rule, Theme

__all__ = ["render_pdf"]

FONT_DIR: Final = Path(__file__).parent / "fonts"
PAGE_WIDTH, PAGE_HEIGHT = LETTER
MARGIN: Final = 0.9 * inch
CONTENT_WIDTH: Final = PAGE_WIDTH - 2 * MARGIN


@lru_cache(maxsize=1)
def _register_fonts() -> None:
    """Register the bundled faces once per process."""
    for name, filename in (
        ("ScraprSans", "NotoSans-Regular.ttf"),
        ("ScraprSans-Bold", "NotoSans-Bold.ttf"),
        ("ScraprSerif", "NotoSerif-Regular.ttf"),
        ("ScraprSerif-Bold", "NotoSerif-Bold.ttf"),
    ):
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / filename)))
    pdfmetrics.registerFontFamily(
        "ScraprSans", normal="ScraprSans", bold="ScraprSans-Bold",
        italic="ScraprSans", boldItalic="ScraprSans-Bold",
    )
    pdfmetrics.registerFontFamily(
        "ScraprSerif", normal="ScraprSerif", bold="ScraprSerif-Bold",
        italic="ScraprSerif", boldItalic="ScraprSerif-Bold",
    )


def _font(serif: bool, bold: bool = False) -> str:
    base = "ScraprSerif" if serif else "ScraprSans"
    return f"{base}-Bold" if bold else base


def _colour(value: str) -> colors.Color:
    return colors.HexColor(value)


class _Styles:
    """Paragraph styles for one theme."""

    def __init__(self, theme: Theme) -> None:
        body = _font(theme.body_serif)
        heading = _font(theme.heading_serif, theme.heading_bold)
        text, muted, accent = _colour(theme.text), _colour(theme.muted), _colour(theme.accent)
        scale = theme.title_scale

        self.cover_title = ParagraphStyle(
            "cover_title", fontName=heading, fontSize=28 * scale, leading=34 * scale,
            textColor=text, alignment=TA_LEFT,
        )
        self.cover_title_on_accent = ParagraphStyle(
            "cover_title_on_accent", parent=self.cover_title,
            textColor=_colour(theme.accent_on_accent),
        )
        self.h1 = ParagraphStyle(
            "h1", fontName=heading, fontSize=19 * scale, leading=24 * scale,
            textColor=text, spaceBefore=18, spaceAfter=10,
        )
        self.h2 = ParagraphStyle(
            "h2", fontName=heading, fontSize=13, leading=17, textColor=text,
            spaceBefore=10, spaceAfter=6,
        )
        self.body = ParagraphStyle(
            "body", fontName=body, fontSize=10.5, leading=15.5, textColor=text,
        )
        self.label = ParagraphStyle(
            "label", fontName=_font(theme.body_serif, True), fontSize=8.5, leading=11,
            textColor=muted,
        )
        self.meta = ParagraphStyle(
            "meta", fontName=body, fontSize=8.5, leading=12, textColor=muted,
        )
        self.accent_meta = ParagraphStyle(
            "accent_meta", parent=self.meta, textColor=accent,
        )
        self.reference = ParagraphStyle(
            "reference", fontName=body, fontSize=8.5, leading=12.5, textColor=text,
            leftIndent=18, firstLineIndent=-18, spaceAfter=3,
        )


def _text(value: str) -> str:
    """Escape untrusted text for paragraph markup."""
    return escape(value)


def _refs(numbers: Sequence[int]) -> str:
    return "".join(f"[{number}]" for number in numbers)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


def _page_decorator(document: ExportDocument, theme: Theme, *, cover: bool):  # type: ignore[no-untyped-def]
    ground, muted, accent = _colour(theme.ground), _colour(theme.muted), _colour(theme.accent)

    def draw(canvas: Canvas, doc: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFillColor(ground)
        canvas.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)

        if theme.cover is Cover.BAND:
            canvas.setFillColor(accent)
            height = 0.55 * inch if cover else 0.12 * inch
            canvas.rect(0, PAGE_HEIGHT - height, PAGE_WIDTH, height, stroke=0, fill=1)

        # `REQ-EXP-006 AC-2`: every page says which version it came from.
        canvas.setFont(_font(theme.body_serif), 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(MARGIN, 0.5 * inch, document.version_line)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def _title_size(objective: str, base: float) -> float:
    """Smaller type for a longer objective, so a long brief still fits a cover."""
    length = len(objective)
    if length <= 90:
        return base
    if length <= 180:
        return base * 0.78
    if length <= 400:
        return base * 0.58
    return base * 0.45


def _cover(document: ExportDocument, theme: Theme, styles: _Styles) -> list[Flowable]:
    flow: list[Flowable] = []
    on_accent = theme.cover is Cover.ACCENT_BLOCK
    flow.append(Spacer(1, 1.1 * inch if theme.cover is not Cover.PLAIN else 2.2 * inch))

    base = styles.cover_title_on_accent if on_accent else styles.cover_title
    size = _title_size(document.objective, base.fontSize)
    title_style = ParagraphStyle("cover_title_sized", parent=base, fontSize=size, leading=size * 1.2)

    meta_colour = theme.accent_on_accent if on_accent else theme.muted
    meta_style = ParagraphStyle("cover_meta", parent=styles.meta, fontSize=10, leading=14,
                                textColor=_colour(meta_colour))

    header: list[Flowable] = [Paragraph(_text(document.objective), title_style), Spacer(1, 14)]
    if document.subject:
        header.append(Paragraph(_text(f"Subject: {document.subject}"), meta_style))
    header.append(Paragraph(_text(document.version_line), meta_style))

    if on_accent:
        # A block that grows with its content, so the title and the version
        # line are always on the accent, however long the objective is.
        block = Table([[header]], colWidths=[CONTENT_WIDTH])
        block.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _colour(theme.accent)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 28),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 28),
                    ("TOPPADDING", (0, 0), (-1, -1), 40),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 36),
                ]
            )
        )
        flow.append(block)
    else:
        flow.extend(header)

    if theme.cover is Cover.ACCENT_RULE:
        flow.append(Spacer(1, 14))
        flow.append(_Rule(1.6 * inch, 3, theme.accent))

    if theme.cover is Cover.FIGURES_STRIP:
        flow.append(Spacer(1, 24))
        flow.append(_figures_strip(document, theme, styles))

    if document.is_partial:
        flow.append(Spacer(1, 22))
        flow.append(
            Paragraph(
                "Parts of this question could not be answered. What is missing is "
                "marked in the report, and nothing has been filled in with guesswork.",
                styles.body,
            )
        )
    flow.append(PageBreak())
    return flow


def _figures_strip(document: ExportDocument, theme: Theme, styles: _Styles) -> Flowable:
    """Up to three figures from the report's charts, or its shape if it has none."""
    cells: list[tuple[str, str]] = []
    for section in document.sections:
        for chart in section.charts:
            for series in chart.series:
                if series.points and len(cells) < 3:
                    label, value = series.points[-1]
                    cells.append((format_value(value, chart.unit), f"{chart.title or series.name}, {label}"))
    if not cells:
        claims = sum(len(section.claims) for section in document.sections)
        cells = [
            (str(claims), "findings"),
            (str(len(document.references)), "sources"),
            (str(len(document.sections)), "sections"),
        ]
    big = ParagraphStyle("figure", fontName=_font(theme.heading_serif, True), fontSize=20,
                         leading=24, textColor=_colour(theme.accent))
    row = [[Paragraph(_text(value), big), Paragraph(_text(caption), styles.meta)] for value, caption in cells]
    table = Table([[cell for cell in row]], colWidths=[CONTENT_WIDTH / len(row)] * len(row))
    table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.75, _colour(theme.rule)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


class _Rule(Flowable):
    """A short horizontal accent rule."""

    def __init__(self, width: float, thickness: float, colour: str) -> None:
        super().__init__()
        self.width, self.height = width, thickness
        self._colour = colour

    def draw(self) -> None:
        self.canv.setFillColor(_colour(self._colour))
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def _changes(document: ExportDocument, theme: Theme, styles: _Styles) -> list[Flowable]:
    if document.changes_headline is None:
        return []
    inner: list[Flowable] = [
        Paragraph("What&#8217;s changed", styles.h2),
        Paragraph(_text(document.changes_headline), styles.body),
    ]
    for change in document.changes:
        inner.append(Spacer(1, 6))
        inner.append(Paragraph(_text(change.label), styles.label))
        inner.append(Paragraph(_text(change.summary), styles.body))
    return [_panel(inner, theme), Spacer(1, 16)]


def _panel(inner: list[Flowable], theme: Theme) -> Flowable:
    table = Table([[inner]], colWidths=[CONTENT_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _colour(theme.surface)),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return table


_DASHES: Final[dict[Rule, list[float] | None]] = {
    Rule.SOLID: None,
    Rule.DASHED: [4, 2.5],
    Rule.DOTTED: [1, 2],
}


def _claim(claim: ExportClaim, theme: Theme, styles: _Styles) -> Flowable:
    label = claim.type_word
    if claim.confidence:
        label = f"{label} · {claim.confidence}"
    inner: list[Flowable] = [Paragraph(_text(label), styles.label), Spacer(1, 2)]

    text = _text(claim.text)
    if claim.references:
        text += f' <font color="{theme.accent}" size="8">{_refs(claim.references)}</font>'
    inner.append(Paragraph(text, styles.body))

    if claim.reporting_period:
        inner.append(Paragraph(_text(f"Covers the period ending {claim.reporting_period}"), styles.meta))
    if claim.assumptions:
        inner.append(Paragraph(_text("Assumptions: " + "; ".join(claim.assumptions)), styles.meta))

    for conflict in claim.conflicts:
        rows: list[Flowable] = [
            Paragraph("Sources disagree" if not conflict.resolved else "Sources differ, explained", styles.label),
        ]
        for side in conflict.sides:
            marker = f" [{side.reference}]" if side.reference else ""
            owner = " (your document)" if side.from_your_document else ""
            rows.append(Paragraph(_text(f"{side.value} from {side.source_name}{owner}{marker}"), styles.meta))
        rows.append(Paragraph(_text(conflict.explanation), styles.meta))
        inner.append(Spacer(1, 4))
        inner.append(_panel(rows, theme))

    rule = RULES[claim.claim_type]
    table = Table([[inner]], colWidths=[CONTENT_WIDTH])
    commands: list[Any] = [
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if rule is not Rule.NONE:
        commands.append(
            ("LINEBEFORE", (0, 0), (0, -1), 1.6, _colour(theme.text), None, _DASHES[rule])
        )
    table.setStyle(TableStyle(commands))
    return KeepTogether([table, Spacer(1, 10)])


def _chart(chart: ExportChart, theme: Theme, styles: _Styles) -> list[Flowable]:
    flow: list[Flowable] = [Paragraph(_text(chart.title or "Chart"), styles.h2)]
    if chart.kind in {VizKind.TABLE, VizKind.MATRIX}:
        flow.append(_table_chart(chart, theme, styles))
    elif chart.kind is VizKind.METRIC:
        big = ParagraphStyle("metric", fontName=_font(theme.heading_serif, True), fontSize=24,
                             leading=28, textColor=_colour(theme.accent))
        for series in chart.series:
            for label, value in series.points:
                flow.append(Paragraph(_text(format_value(value, chart.unit)), big))
                flow.append(Paragraph(_text(f"{series.name} {label}".strip()), styles.meta))
    else:
        flow.append(_drawing(chart, theme))
    if chart.references:
        flow.append(Paragraph(_text(f"Sources: {_refs(chart.references)}"), styles.meta))
    return [KeepTogether(flow), Spacer(1, 12)]


def _table_chart(chart: ExportChart, theme: Theme, styles: _Styles) -> Flowable:
    header = [Paragraph("Series", styles.label), Paragraph("Label", styles.label),
              Paragraph("Value", styles.label)]
    rows = [header] + [
        [Paragraph(_text(series.name), styles.meta), Paragraph(_text(label), styles.meta),
         Paragraph(_text(format_value(value, chart.unit)), styles.meta)]
        for series in chart.series
        for label, value in series.points
    ]
    table = Table(rows, colWidths=[CONTENT_WIDTH * 0.4, CONTENT_WIDTH * 0.3, CONTENT_WIDTH * 0.3])
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, _colour(theme.rule))]))
    return table


def _drawing(chart: ExportChart, theme: Theme) -> Drawing:
    """A line or bar chart from the spec's points."""
    width, height = CONTENT_WIDTH, 2.4 * inch
    left, bottom, top = 48.0, 28.0, 10.0
    plot_w, plot_h = width - left - 10, height - bottom - top
    drawing = Drawing(width, height)
    text, muted, rule = _colour(theme.text), _colour(theme.muted), _colour(theme.rule)
    font = _font(theme.body_serif)

    labels = chart.labels
    values = [value for series in chart.series for _, value in series.points]
    low = min(min(values), Decimal(0))
    high = max(values) if max(values) > low else low + 1
    span = high - low

    def y_for(value: Decimal) -> float:
        return bottom + float((value - low) / span) * plot_h

    for step in range(5):
        level = low + span * Decimal(step) / 4
        y = y_for(level)
        drawing.add(Line(left, y, left + plot_w, y, strokeColor=rule, strokeWidth=0.5))
        drawing.add(String(left - 4, y - 3, format_value(level, None), fontName=font,
                           fontSize=6.5, fillColor=muted, textAnchor="end"))

    slot = plot_w / max(1, len(labels))
    for index, label in enumerate(labels):
        drawing.add(String(left + slot * (index + 0.5), bottom - 14, label[:18], fontName=font,
                           fontSize=6.5, fillColor=muted, textAnchor="middle"))

    palette = [theme.accent] + [theme.neutral_series] * max(0, len(chart.series) - 1)
    if chart.kind is VizKind.LINE:
        for series_index, series in enumerate(chart.series):
            colour = _colour(palette[series_index])
            points: list[float] = []
            for label, value in series.points:
                x = left + slot * (labels.index(label) + 0.5)
                points.extend([x, y_for(value)])
                if series_index == 0:
                    drawing.add(Circle(x, y_for(value), 2.6, fillColor=colour, strokeColor=colour))
                else:
                    drawing.add(_filled_rect(x - 2.4, y_for(value) - 2.4, 4.8, 4.8, colour))
            line = PolyLine(points, strokeColor=colour, strokeWidth=1.6)
            if series_index:
                line.strokeDashArray = [4, 2]
            drawing.add(line)
    else:
        groups = max(1, len(chart.series))
        bar_w = slot * 0.7 / groups
        for series_index, series in enumerate(chart.series):
            colour = _colour(palette[series_index])
            for label, value in series.points:
                x = left + slot * labels.index(label) + slot * 0.15 + bar_w * series_index
                y0, y1 = y_for(max(low, Decimal(0))), y_for(value)
                drawing.add(_filled_rect(x, min(y0, y1), bar_w * 0.92, abs(y1 - y0), colour))

    drawing.add(Line(left, bottom, left + plot_w, bottom, strokeColor=text, strokeWidth=0.8))
    if len(chart.series) > 1:
        # A legend whose markers match the plot's: a circle for the accent
        # series, squares for the rest. Drawn, not typed, so it never depends
        # on a font carrying geometric glyphs.
        for series_index, series in enumerate(chart.series):
            colour = _colour(palette[series_index])
            y = height - 8 - 9 * series_index
            drawing.add(String(left + plot_w, y, series.name[:30], fontName=font, fontSize=6.5,
                               fillColor=text, textAnchor="end"))
            marker_x = left + plot_w - pdfmetrics.stringWidth(series.name[:30], font, 6.5) - 7
            if series_index == 0:
                drawing.add(Circle(marker_x, y + 2.2, 2.2, fillColor=colour, strokeColor=colour))
            else:
                drawing.add(_filled_rect(marker_x - 2.2, y, 4.4, 4.4, colour))
    return drawing


def _filled_rect(x: float, y: float, width: float, height: float, colour: colors.Color) -> Rect:
    """A solid rectangle with no outline.

    Colours are set as attributes rather than keyword arguments: the typeshed
    stubs for ReportLab's shape constructors reject the keywords the library
    itself documents and accepts.
    """
    rect = Rect(x, y, width, height)
    rect.fillColor = colour
    rect.strokeColor = None
    return rect


def _references(references: Sequence[ExportReference], theme: Theme, styles: _Styles) -> list[Flowable]:
    if not references:
        return []
    flow: list[Flowable] = [PageBreak(), Paragraph("Sources", styles.h1)]
    for reference in references:
        name = _text(reference.name)
        if reference.url:
            name = f'<link href={quoteattr(reference.url)} color="{theme.accent}">{name}</link>'
        details = [reference.tier, f"read {format_date(reference.retrieved_at)}"]
        if reference.publisher:
            details.insert(0, reference.publisher)
        if reference.from_your_document:
            details.insert(0, "your document")
        if reference.document_removed:
            details.append("file since deleted")
        line = f"[{reference.number}] {name}. {_text(', '.join(details))}."
        if reference.url:
            line += f"<br/>{_text(reference.url)}"
        flow.append(Paragraph(line, styles.reference))
    return flow


def render_pdf(document: ExportDocument, theme: Theme) -> bytes:
    """Render one document in one theme."""
    _register_fonts()
    styles = _Styles(theme)
    buffer = io.BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=document.objective[:200],
        subject=document.version_line,
        author="ScrapR",
        creator="ScrapR",
        # No timestamps or random ids in the file: the same version in the
        # same theme is the same bytes, which is what makes a retried export
        # indistinguishable from the first attempt (`NFR-REL-003`).
        invariant=True,
    )

    story: list[Flowable] = _cover(document, theme, styles)
    story.extend(_changes(document, theme, styles))

    for section in document.sections:
        story.append(Paragraph(_text(section.title), styles.h1))
        for claim in section.claims:
            story.append(_claim(claim, theme, styles))
        for chart in section.charts:
            story.extend(_chart(chart, theme, styles))

    story.extend(_references(document.references, theme, styles))

    template.build(
        story,
        onFirstPage=_page_decorator(document, theme, cover=True),
        onLaterPages=_page_decorator(document, theme, cover=False),
    )
    return buffer.getvalue()
