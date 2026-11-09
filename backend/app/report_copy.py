"""Templated report copy, keyed by Holland trait.

Next steps are deliberately not here — they are per-career, authored in
`seeds/career-guidance.yaml`, because advice tied to a specific career is a direction and
advice tied to a trait is a platitude.

This is the layer an LLM replaces. Until then it produces a report that is honest — every line
is traceable to a trait score — if necessarily generic. Keeping it in one module means the
swap is a change of source, not a rewrite of the assembly in `app/reports.py`.

Written to be read by a sixteen-year-old: short sentences, concrete nouns, no jargon.
"""

from app.models import HollandTrait

TRAIT_LABELS: dict[HollandTrait, str] = {
    HollandTrait.REALISTIC: "Hands-on",
    HollandTrait.INVESTIGATIVE: "Analytical",
    HollandTrait.ARTISTIC: "Creative",
    HollandTrait.SOCIAL: "People-centred",
    HollandTrait.ENTERPRISING: "Persuasive",
    HollandTrait.CONVENTIONAL: "Structured",
}

TRAIT_BLURBS: dict[HollandTrait, str] = {
    HollandTrait.REALISTIC: (
        "You like work with a visible result — things you can build, fix, operate or improve."
    ),
    HollandTrait.INVESTIGATIVE: (
        "You like understanding why something works before deciding what to do about it."
    ),
    HollandTrait.ARTISTIC: (
        "You like making things, and you'd rather invent an approach than follow one."
    ),
    HollandTrait.SOCIAL: (
        "You're drawn to work where the point is another person being better off."
    ),
    HollandTrait.ENTERPRISING: (
        "You like momentum — persuading people, making calls, and being held to the outcome."
    ),
    HollandTrait.CONVENTIONAL: ("You like order, and you notice when something doesn't add up."),
}

STRENGTHS: dict[HollandTrait, tuple[str, str]] = {
    HollandTrait.REALISTIC: (
        "Practical problem-solving",
        "You answered towards work with a tangible result, which usually means you learn "
        "fastest by trying something rather than reading about it.",
    ),
    HollandTrait.INVESTIGATIVE: (
        "Getting to the bottom of things",
        "Your answers lean towards understanding causes. That patience is the core skill in "
        "research, diagnostics and anything involving data.",
    ),
    HollandTrait.ARTISTIC: (
        "Original thinking",
        "You picked open-ended work over prescribed work. That's the instinct behind design, "
        "writing and anything where the brief is vague on purpose.",
    ),
    HollandTrait.SOCIAL: (
        "Reading people",
        "Your answers point towards work centred on other people. That's a genuine skill, and "
        "it's the one most often left off a CV.",
    ),
    HollandTrait.ENTERPRISING: (
        "Getting things moving",
        "You leaned towards leading and persuading. People who are comfortable being "
        "accountable for an outcome are rarer than you'd think.",
    ),
    HollandTrait.CONVENTIONAL: (
        "Accuracy and follow-through",
        "You favoured clear structure. Work where a small error has real consequences depends "
        "on exactly this.",
    ),
}

# Where a strength, overplayed, becomes a problem.
STRENGTH_CHALLENGES: dict[HollandTrait, tuple[str, str]] = {
    HollandTrait.REALISTIC: (
        "Jumping in before planning",
        "Preferring to act can mean starting before the problem is clear. On bigger projects "
        "that costs more time than the planning would have.",
    ),
    HollandTrait.INVESTIGATIVE: (
        "Researching past the point of usefulness",
        "Wanting to understand everything first can stall a decision that only needed to be "
        "good enough. Notice when you're reading instead of choosing.",
    ),
    HollandTrait.ARTISTIC: (
        "Struggling with constraints",
        "Most paid creative work comes with a brief, a budget and someone else's opinion. "
        "That's the job, not an obstacle to it.",
    ),
    HollandTrait.SOCIAL: (
        "Taking on other people's problems",
        "People-centred work runs on empathy and burns out on it too. The skill to build "
        "early is saying no without guilt.",
    ),
    HollandTrait.ENTERPRISING: (
        "Moving faster than the detail allows",
        "Momentum is an advantage right up until something important gets skipped. Pair "
        "yourself with someone who reads the small print.",
    ),
    HollandTrait.CONVENTIONAL: (
        "Over-relying on the established way",
        "Structure is valuable, but processes outlive their usefulness. Ask what a rule was "
        "for before defending it.",
    ),
}

# Where a low score suggests something worth deliberately practising.
DEVELOPMENT_AREAS: dict[HollandTrait, tuple[str, str]] = {
    HollandTrait.REALISTIC: (
        "Hands-on confidence",
        "You scored low on practical work. Worth testing rather than assuming — plenty of "
        "people discover they like building things once they've actually tried.",
    ),
    HollandTrait.INVESTIGATIVE: (
        "Sitting with hard problems",
        "Analytical work scored low for you. Almost every career eventually asks you to work "
        "something out from scratch, so it's worth some practice.",
    ),
    HollandTrait.ARTISTIC: (
        "Working without a brief",
        "You favoured clearer tasks over open ones. Comfort with ambiguity is learnable, and "
        "it's what separates senior roles from junior ones.",
    ),
    HollandTrait.SOCIAL: (
        "Collaboration",
        "People-centred work scored low. Very few jobs are solitary, so time spent getting "
        "comfortable explaining your thinking pays off everywhere.",
    ),
    HollandTrait.ENTERPRISING: (
        "Speaking up for your own work",
        "You leaned away from persuading and leading. You don't have to enjoy it, but being "
        "able to argue for your ideas is not optional.",
    ),
    HollandTrait.CONVENTIONAL: (
        "Consistency on the dull parts",
        "Structured work scored low. The unglamorous, repeatable half of a job is usually "
        "what builds trust in the interesting half.",
    ),
}
