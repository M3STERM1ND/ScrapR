"""The six export themes (`REQ-EXP-003`, `DEC-22`).

Static definitions. Nothing about a theme is chosen per export, and no model
touches any of it (`REQ-EXP-004 AC-1`): an export is the version's content
poured into one of these, which is why a PDF in "Dark" costs exactly what a PDF
in "Minimal" does (`AC-2`).

**Colour carries identity, never meaning.** The distinctions `REQ-EXP-009`
requires — claim type, confidence, conflict — are carried by words and rule
treatment in every theme (`RULES` below), so they survive a greyscale print of
any of them (`NFR-USE-002`, `DEC-11 §5`).

**Contrast is a property of the definition.** Text and muted text meet WCAG AA
against the ground in every theme; `contrast_ratio` is here so a test can hold
each definition to it, rather than a reviewer's eye.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final, final

from scrapr_core.db.enums import ClaimType, ExportTheme

__all__ = [
    "RULES",
    "THEMES",
    "Cover",
    "Rule",
    "Theme",
    "contrast_ratio",
    "theme_for",
]


@unique
class Cover(StrEnum):
    """How the first page or slide is composed."""

    ACCENT_RULE = "accent_rule"
    """Title, then a short accent rule beneath it."""
    FIGURES_STRIP = "figures_strip"
    """Title over a strip of the report's key figures."""
    ACCENT_BLOCK = "accent_block"
    """A full-width accent block behind the title."""
    BAND = "band"
    """An accent band across the top edge."""
    PLAIN = "plain"
    """Title and date. Nothing else."""


@unique
class Rule(StrEnum):
    """The left rule beside a claim, by type (`REQ-SYNTH-002 AC-3`)."""

    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    NONE = "none"


RULES: Final[dict[ClaimType, Rule]] = {
    ClaimType.FACT: Rule.SOLID,
    ClaimType.ANALYSIS: Rule.DASHED,
    ClaimType.FORECAST: Rule.DOTTED,
    ClaimType.UNCERTAINTY: Rule.NONE,
}
"""The workspace's own treatments (`DEC-11 §5`), identical in every theme."""


@final
@dataclass(frozen=True, slots=True)
class Theme:
    """One predefined design (`DEC-22`)."""

    key: ExportTheme
    name: str
    ground: str
    surface: str
    """Panels: conflicts, What's Changed."""
    text: str
    muted: str
    accent: str
    rule: str
    """Hairlines and table borders."""
    accent_on_accent: str
    """Text placed on an accent-filled area, such as a cover block."""
    heading_serif: bool
    body_serif: bool
    heading_bold: bool
    cover: Cover
    title_scale: float
    """Relative size of headings, 1.0 being the house default."""
    pptx_heading_font: str
    pptx_body_font: str
    neutral_series: str
    """The colour of chart series after the first, which is always the accent."""


THEMES: Final[dict[ExportTheme, Theme]] = {
    ExportTheme.PROFESSIONAL: Theme(
        key=ExportTheme.PROFESSIONAL,
        name="Professional",
        ground="#FFFFFF",
        surface="#F3F5F8",
        text="#1D2530",
        muted="#52606D",
        accent="#1F4E79",
        rule="#D5DBE3",
        accent_on_accent="#FFFFFF",
        heading_serif=False,
        body_serif=False,
        heading_bold=True,
        cover=Cover.ACCENT_RULE,
        title_scale=1.0,
        pptx_heading_font="Calibri",
        pptx_body_font="Calibri",
        neutral_series="#8A96A3",
    ),
    ExportTheme.INVESTOR: Theme(
        key=ExportTheme.INVESTOR,
        name="Investor",
        ground="#FFFFFF",
        surface="#F2F6F2",
        text="#14213D",
        muted="#4A5568",
        accent="#1B5E20",
        rule="#D4DDD4",
        accent_on_accent="#FFFFFF",
        heading_serif=True,
        body_serif=False,
        heading_bold=True,
        cover=Cover.FIGURES_STRIP,
        title_scale=1.0,
        pptx_heading_font="Georgia",
        pptx_body_font="Calibri",
        neutral_series="#8E9AAF",
    ),
    ExportTheme.MODERN: Theme(
        key=ExportTheme.MODERN,
        name="Modern",
        ground="#FBF7F2",
        surface="#F3EAE0",
        text="#22223B",
        muted="#5C5470",
        accent="#B4442A",
        rule="#E6D9CB",
        accent_on_accent="#FFFFFF",
        heading_serif=False,
        body_serif=False,
        heading_bold=True,
        cover=Cover.ACCENT_BLOCK,
        title_scale=1.2,
        pptx_heading_font="Arial",
        pptx_body_font="Arial",
        neutral_series="#9A8C98",
    ),
    ExportTheme.CORPORATE: Theme(
        key=ExportTheme.CORPORATE,
        name="Corporate",
        ground="#FFFFFF",
        surface="#EEF4F3",
        text="#222222",
        muted="#555555",
        accent="#00695C",
        rule="#D0D7D6",
        accent_on_accent="#FFFFFF",
        heading_serif=False,
        body_serif=False,
        heading_bold=True,
        cover=Cover.BAND,
        title_scale=0.95,
        pptx_heading_font="Calibri",
        pptx_body_font="Calibri",
        neutral_series="#90A4AE",
    ),
    ExportTheme.MINIMAL: Theme(
        key=ExportTheme.MINIMAL,
        name="Minimal",
        ground="#FFFFFF",
        surface="#F5F5F5",
        text="#111111",
        muted="#595959",
        accent="#111111",
        rule="#DDDDDD",
        accent_on_accent="#FFFFFF",
        heading_serif=True,
        body_serif=True,
        heading_bold=False,
        cover=Cover.PLAIN,
        title_scale=1.0,
        pptx_heading_font="Georgia",
        pptx_body_font="Georgia",
        neutral_series="#9E9E9E",
    ),
    ExportTheme.DARK: Theme(
        key=ExportTheme.DARK,
        name="Dark",
        ground="#0F1419",
        surface="#1A2129",
        text="#E6EDF3",
        muted="#9DA7B3",
        accent="#39C5CF",
        rule="#2D3640",
        accent_on_accent="#0F1419",
        heading_serif=False,
        body_serif=False,
        heading_bold=True,
        cover=Cover.ACCENT_RULE,
        title_scale=1.0,
        pptx_heading_font="Arial",
        pptx_body_font="Arial",
        neutral_series="#6E7781",
    ),
}


def theme_for(key: ExportTheme) -> Theme:
    return THEMES[key]


def _channel(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    digits = hex_colour.lstrip("#")
    red, green, blue = (int(digits[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2 contrast ratio between two `#RRGGBB` colours."""
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)
