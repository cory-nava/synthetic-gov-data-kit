"""Invariants on `USHouseholdProfile` itself, independent of any generator."""

import pytest
from govsynth.profiles.us_household import PHRASING_STYLES, SCENARIO_PHRASING_STYLES, USHouseholdProfile


def test_age_60_plus_cannot_be_labelled_non_elderly() -> None:
    """A head aged 60+ IS an elderly member (7 CFR 271.2) -- the flag cannot say otherwise.

    Regression guard. Before this invariant, 14% of SNAP threshold profiles
    rendered prose stating an age of 60+ ("... is a 63-year-old single parent
    ...") while carrying has_elderly_or_disabled=False, so the expected
    reasoning applied the gross income test that 7 CFR 273.9(a)(2) waives for
    such households. A model fine-tuned on that corpus read the stated age,
    correctly inferred elderly status, and was scored against a label that
    contradicted the prompt.
    """
    with pytest.raises(ValueError, match="7 CFR 271.2"):
        USHouseholdProfile(
            household_size=2,
            monthly_gross_income=1000.0,
            state="WY",
            age_of_head=63,
            has_elderly_or_disabled=False,
        )

    # The invariant is one-directional: a head under 60 may still carry the
    # flag (a disabled member, or an elderly member who is not the head).
    USHouseholdProfile(
        household_size=2,
        monthly_gross_income=1000.0,
        state="WY",
        age_of_head=40,
        has_elderly_or_disabled=True,
    )


def test_no_sampling_path_produces_the_contradiction() -> None:
    """The invariant must hold for every sampled profile, not just hand-built ones.

    `at_threshold` is the path the generators actually use, so a sampling site
    that sets age and the elderly flag independently would raise here rather
    than surfacing later as a mislabelled case.
    """
    for threshold in ("gross_income_limit", "net_income_limit", "asset_limit_general"):
        for seed in range(60):
            profile = USHouseholdProfile.at_threshold(
                program="snap",
                threshold=threshold,
                state="WY",
                household_size=3,
                fiscal_year=2026,
                offset_pct=0.05,
                seed=seed,
            )
            assert not (profile.age_of_head >= 60 and not profile.has_elderly_or_disabled)


def _make_profile(**overrides) -> USHouseholdProfile:
    defaults = dict(
        household_size=3,
        monthly_gross_income=2400.0,
        state="OH",
        liquid_assets=850.0,
        has_elderly_or_disabled=False,
        has_dependent_children=True,
        age_of_head=34,
        head_of_household_name="Jordan Rivera",
        city="Columbus",
    )
    defaults.update(overrides)
    return USHouseholdProfile(**defaults)


def test_unknown_phrasing_style_raises() -> None:
    """A typo'd style name must fail loudly, not silently fall back to one template
    -- that would quietly collapse the diversity a caller asked for."""
    profile = _make_profile()
    with pytest.raises(ValueError, match="unknown phrasing style"):
        profile.natural_language_summary_styled("dramatic_reading")


def test_every_declared_style_renders_without_error() -> None:
    profile = _make_profile()
    for style in PHRASING_STYLES:
        text = profile.natural_language_summary_styled(style)
        assert text and text.strip(), style


def test_styles_produce_genuinely_different_text() -> None:
    """The whole point: these must not be five names for the same sentence."""
    profile = _make_profile()
    rendered = {style: profile.natural_language_summary_styled(style) for style in PHRASING_STYLES}
    assert len(set(rendered.values())) == len(PHRASING_STYLES)


def test_all_styles_state_the_same_facts() -> None:
    """The safety property this whole feature depends on: varying phrasing must
    never vary WHICH facts are stated or the numbers attached to them. A
    style that dropped, say, the elderly flag would make that training record
    easier than its siblings for a reason that has nothing to do with SNAP
    policy -- see the module docstring on `natural_language_summary_styled`.
    """
    profile = _make_profile(has_elderly_or_disabled=False, age_of_head=34)
    income_str = f"${profile.monthly_gross_income:,.0f}"
    assets_str = f"${profile.liquid_assets:,.0f}"

    default = profile.natural_language_summary()
    assert income_str in default
    assert assets_str in default

    for style in PHRASING_STYLES:
        text = profile.natural_language_summary_styled(style)
        assert income_str in text, (style, text)
        assert assets_str in text, (style, text)
        assert profile.city in text, (style, text)
        assert profile.state in text, (style, text)
        assert str(profile.age_of_head) in text, (style, text)


def test_elderly_flag_appears_in_every_style_when_true_and_no_style_when_false() -> None:
    elderly_profile = _make_profile(has_elderly_or_disabled=True, age_of_head=65)
    non_elderly_profile = _make_profile(has_elderly_or_disabled=False, age_of_head=34)

    for style in PHRASING_STYLES:
        elderly_text = elderly_profile.natural_language_summary_styled(style).lower()
        non_elderly_text = non_elderly_profile.natural_language_summary_styled(style).lower()
        assert "elderly" in elderly_text or "60" in elderly_text, (style, elderly_text)
        assert "elderly" not in non_elderly_text and "disabled" not in non_elderly_text, (style, non_elderly_text)


def test_citizenship_status_appears_only_for_noncitizens_across_every_style() -> None:
    from govsynth.models.enums import CitizenshipStatus

    citizen = _make_profile(citizenship_status=CitizenshipStatus.CITIZEN)
    noncitizen = _make_profile(citizenship_status=CitizenshipStatus.QUALIFIED_ALIEN)

    for style in PHRASING_STYLES:
        citizen_text = citizen.natural_language_summary_styled(style)
        noncitizen_text = noncitizen.natural_language_summary_styled(style)
        assert "qualified alien" not in citizen_text.lower(), (style, citizen_text)
        assert "qualified alien" in noncitizen_text.lower(), (style, noncitizen_text)


def test_zero_assets_never_states_a_dollar_figure_for_savings() -> None:
    """Every style has a branch for `liquid_assets == 0` -- guard it explicitly
    rather than trusting that "$0 in savings" would be harmless; stating a
    literal $0 reads as a distinct, verified fact rather than "not reported","""
    profile = _make_profile(liquid_assets=0.0)
    for style in PHRASING_STYLES:
        text = profile.natural_language_summary_styled(style)
        assert "$0" not in text, (style, text)


def test_unknown_scenario_phrasing_style_raises() -> None:
    """A typo'd scenario style name must fail loudly, not silently fall back."""
    profile = _make_profile()
    with pytest.raises(ValueError, match="unknown scenario phrasing style"):
        profile.generate_scenario_question_styled("fictional_style")


def test_every_scenario_style_renders_without_error() -> None:
    """All declared scenario phrasing styles must produce non-empty strings."""
    profile = _make_profile()
    for style in SCENARIO_PHRASING_STYLES:
        question = profile.generate_scenario_question_styled(style)
        assert question and question.strip(), style
        assert "SNAP" in question, (style, question)


def test_scenario_styles_produce_genuinely_different_text() -> None:
    """Each scenario phrasing style must produce distinct text."""
    profile = _make_profile()
    rendered = {style: profile.generate_scenario_question_styled(style) for style in SCENARIO_PHRASING_STYLES}
    assert len(set(rendered.values())) == len(SCENARIO_PHRASING_STYLES)


def test_all_scenario_styles_mention_the_program() -> None:
    """All scenario styles must reference SNAP.

    Safety property: every question must make clear what program is being asked about.
    """
    profile = _make_profile()
    for style in SCENARIO_PHRASING_STYLES:
        question = profile.generate_scenario_question_styled(style, "snap")
        assert "SNAP" in question, (style, question)
