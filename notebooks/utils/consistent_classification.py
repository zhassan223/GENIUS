"""
Three-Stage Classification Pipeline with Canonical Mechanism Registry

PROBLEM: Structurally equivalent policies (same causal mechanism) get different
labels when classified independently row-by-row. This happens across cities,
across sectors, and even within the same document.

SOLUTION: Separate mechanism extraction from classification. Build a canonical
registry of mechanisms, classify each mechanism ONCE, then propagate labels
to all policies that share that mechanism.

STAGE 1: Extract and canonicalize causal mechanisms from every policy
STAGE 2: Group by canonical mechanism, classify one representative per group,
          propagate labels to all members
STAGE 3: Enrich each policy with instance-specific metadata (instrument type,
          directness, climate relevance, location-based secondaries)
"""

import dspy
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Literal

from pydantic import BaseModel, Field


# =============================================================================
# SHARED REFERENCE TEXT (used across multiple signatures)
# =============================================================================

CATEGORY_DEFINITIONS = """
CATEGORY DEFINITIONS:

1. MITIGATION — Acts on the climate system itself

   Definition: Mitigation policies are designed to influence the climate system
   by changing the amount of greenhouse gases humans release into the atmosphere
   or by increasing the ability of natural or engineered systems to remove those
   gases from the atmosphere. These are forward-looking policies concerned with
   preventing future climate change rather than responding to impacts already
   occurring.

   Primary causal pathway:
       Human activity → DECREASED GHG emissions OR INCREASED carbon sinks
       → DECREASED atmospheric forcing

   What to look for:
   - Direct or indirect emissions reduction targets
   - Energy system transformation (fossil fuels → renewables)
   - Decarbonization targets or net-zero commitments
   - Carbon sequestration framed in climate terms
   - Success measured by emissions reduced or carbon sequestered

   Typical mechanisms:
   - Renewable energy mandates and deployment targets
   - Fleet and vehicle electrification
   - Building electrification (fuel switching from gas to electric)
   - Carbon pricing, offsets, or caps
   - Carbon capture and storage (CCS)
   - Modal shift targets (reduced vehicle miles traveled)
   - Landfill diversion (avoids methane — inherent Mitigation co-benefit)

   Typical causal chains:
   - Renewable energy deployment → fossil generation displacement → ↓ GHG
   - Fleet electrification → eliminated tailpipe emissions → ↓ GHG
   - Modal shift to transit/cycling → reduced VMT → ↓ transport GHG
   - Waste diversion from landfill → avoided methane release → ↓ GHG
   - Carbon pricing → internalized externality → behavioral change → ↓ GHG
   - Net-zero target → economy-wide decarbonization pathway → ↓ GHG


2. ADAPTATION — Responds to climate impacts and builds resilience

   Definition: Adaptation policies reduce the negative consequences of climate
   change by helping people, ecosystems, and infrastructure cope with current
   or expected climate impacts. Unlike mitigation, adaptation does not attempt
   to slow climate change itself — it assumes climate change is happening and
   focuses on reducing harm. Resilience is core: the capacity to continue
   functioning under climate stress and recover quickly after disruption.

   Primary causal pathway:
       Climate hazard → DECREASED exposure OR DECREASED sensitivity
       OR INCREASED adaptive capacity

   What to look for:
   - Explicit reference to climate impacts (heat, flooding, drought,
     sea-level rise, storms, wildfire)
   - Resilience, preparedness, or risk reduction language
   - Protection of people, assets, or systems from future climate conditions
   - Success measured by reduced losses, maintained services, avoided
     disruption, or increased preparedness

   Typical mechanisms:
   - Flood defenses and stormwater infrastructure
   - Heat action plans and cooling centers
   - Climate-resilient infrastructure design standards
   - Emergency response and early warning systems
   - Insurance and risk-transfer instruments
   - Drought preparedness and water supply resilience
   - Adaptation planning frameworks and monitoring systems
   - Occupational heat-safety regulations

   Typical causal chains:
   - Flood defense → reduced flood exposure → ↓ damage/losses
   - Heat action plan → reduced heat exposure → ↓ mortality
   - Climate-resilient codes → reduced sensitivity to extreme weather → ↓ damage
   - Adaptation plan adoption → increased preparedness → ↑ adaptive capacity
   - Water supply expansion → reduced supply vulnerability → ↑ adaptive capacity
   - Outdoor work restrictions during heat → reduced exposure → ↓ health impacts


3. RESOURCE EFFICIENCY — Optimizes resource use regardless of climate

   Definition: Resource efficiency policies deliver the same level of service,
   output, or quality of life while using fewer physical resources (energy,
   water, materials, land). The core idea is that systems are inefficient and
   consume more than necessary. These policies can be fully understood and
   justified even in the absence of climate change concerns — efficiency is
   the goal, not climate impact.

   Primary causal pathway:
       Same service → DECREASED energy/water/material input
       OR DECREASED waste output

   What to look for:
   - Efficiency improvements that don't require climate justification
   - Optimization of inputs, flows, or lifecycles
   - Performance-based standards and benchmarks
   - Success measured by resource savings per unit of output
   - Emissions reduction is a secondary consequence, NOT the primary goal

   Typical mechanisms:
   - Building energy efficiency codes and performance standards
   - Water efficiency standards and per-capita reduction targets
   - Circular economy policies
   - Waste reduction and recycling mandates
   - Industrial process optimization
   - Building operational optimization (tune-ups, retro-commissioning)
   - Lighting replacement programs
   - Appliance and equipment efficiency standards

   Typical causal chains:
   - Building energy code → reduced kWh/m² → same comfort with less energy
   - Water efficiency standard → reduced gallons/person/day → same service less water
   - Building operational optimization → optimized HVAC/controls → same comfort less energy
   - Lighting upgrade → same illumination with less electricity
   - Recycling mandate → same consumption with less virgin material input
   - Waste diversion target → same consumption with less landfill output

   IMPORTANT — When Resource Efficiency is NOT the right primary:
   - If the policy's DOMINANT quantitative climate effect flows through
     emissions reduction (e.g., avoided landfill methane, reduced VMT),
     Mitigation should be primary even if an efficiency framing is possible.
   - Resource Efficiency is primary only when the main point is delivering
     the same service with less input, and emissions reduction is a secondary
     consequence.


4. NATURE-BASED SOLUTIONS (NbS) — Uses ecosystems as infrastructure

   Definition: Nature-based solutions intentionally use natural systems or
   ecological processes to address environmental, climate, or societal
   challenges. Effectiveness depends on the functioning of living systems
   rather than solely on engineered infrastructure. These are defined by
   their MECHANISM (ecosystems), not their outcome. If removing the
   ecological element would fundamentally break the policy's effectiveness,
   it's NbS.

   Primary causal pathway:
       Ecosystem protection/restoration → climate mitigation OR resilience
       OR broader co-benefits

   What to look for:
   - Explicit use of ecosystems as infrastructure
   - Restoration, conservation, or enhancement of natural systems
   - Solutions that rely on biological processes (carbon uptake, water
     absorption, evapotranspiration, cooling)
   - Policy effectiveness depends on living systems functioning properly

   Typical mechanisms:
   - Urban tree planting and canopy targets
   - Wetland restoration and creation
   - Green roofs and green corridors
   - Mangrove and coastal ecosystem restoration
   - Riparian buffers and watershed protection
   - Soil restoration and regenerative agriculture
   - Afforestation and reforestation programs
   - Park system expansion for heat/stormwater management
   - Green belt and shelterbelt establishment

   Typical causal chains:
   - Urban canopy expansion → shading + evapotranspiration → ↓ heat + ↑ carbon uptake
   - Wetland restoration → water absorption + filtration → ↓ flood risk + biodiversity
   - Afforestation/green belts → sand/soil stabilization → ↓ desertification exposure
   - Mangrove restoration → wave attenuation → ↓ coastal flood risk
   - Regenerative agriculture → soil carbon increase → ↑ carbon sinks + ↓ erosion
   - Park expansion → stormwater infiltration + cooling → ↓ heat and flood exposure
"""

EDGE_CASES_REFERENCE = """
EDGE CASES AND OVERRIDES:

Green roofs:
- Urban heat exposure / stormwater management → Adaptation
- Ecosystem restoration / biodiversity → NbS
- Carbon storage → Mitigation (secondary)
- Energy demand reduction → Resource Efficiency (secondary)

Passive cooling/shading:
- Protecting occupants during heatwaves → Adaptation
- Reducing energy demand for cooling → Resource Efficiency

District energy:
- Decarbonizing heating/cooling → Mitigation
- Energy reliability during extreme weather → Adaptation

Floodable parks/wetlands:
- Reducing flood risk through natural absorption → NbS + Adaptation
- Biodiversity enhancement only (no climate risk framing) → NbS only

Infrastructure hardening:
- Strengthening grids/systems for storms/heat → Adaptation
- NOT Mitigation unless emissions reduction is the main objective

Landfill diversion (any waste stream):
- ALL landfill diversion unavoidably avoids methane → Mitigation is ALWAYS
  a justified secondary type, regardless of waste stream
- Primary may be Resource Efficiency (same consumption, less waste) or
  Mitigation (if emissions framing dominates)

Transit-oriented development / housing near transit:
- Dominant climate effect is reduced VMT → Mitigation primary
- Resource Efficiency is secondary (less infrastructure per capita)
- Do NOT default to Resource Efficiency as primary

Building electrification (combustion fuel → electric):
- Dominant effect is eliminating combustion emissions → Mitigation primary
- Resource Efficiency secondary only if efficiency gains are also explicit

EV charging infrastructure:
- Enabling mechanism for transport electrification → Mitigation
- Not Resource Efficiency (enables fuel switching, not input reduction)

Energy benchmarking / disclosure:
- Enabling instrument (information → voluntary action → efficiency)
- Still Resource Efficiency but instrument_directness = 'enabling'

Water reduction in water-stressed locations:
- Resource Efficiency primary (same service, less water)
- Adaptation secondary when location faces drought or water supply threats

Urban tree canopy:
- NbS primary (ecosystem-mediated mechanism)
- Adaptation secondary (heat reduction, stormwater)
- Mitigation secondary (carbon sequestration)

Composting / organic waste diversion:
- Resource Efficiency primary (nutrient/material recovery)
- Mitigation secondary (avoided landfill methane)

General development finance / foreign aid:
- Climate relevance: 'peripheral' unless explicitly climate-targeted
- Do not classify as Adaptation unless climate objectives are stated

Sanitation / wastewater treatment upgrades:
- Climate relevance: 'peripheral' or 'indirect'
- Resource Efficiency if framed as efficiency improvement
- Adaptation only if explicitly framed as climate-resilient infrastructure
"""


CLASSIFICATION_SCHEMA_VERSION = "guide_secondary_typology_v1"
SECONDARY_PROFILE = "policy_text_evidence_gated"

SECONDARY_CODE_TO_CATEGORY = {
    "M-1": "Resource Efficiency",
    "M-2": "Adaptation",
    "M-3": "Nature-Based Solutions",
    "RE-1": "Mitigation",
    "RE-2": "Adaptation",
    "RE-3": "Nature-Based Solutions",
    "NBS-1": "Adaptation",
    "NBS-2": "Mitigation",
    "NBS-3": "Resource Efficiency",
    "A-1": "Nature-Based Solutions",
    "A-2": "Resource Efficiency",
    "A-3": "Mitigation",
}

PRIMARY_TO_ALLOWED_TYPOLOGY_CODES = {
    "Mitigation": ("M-1", "M-2", "M-3"),
    "Resource Efficiency": ("RE-1", "RE-2", "RE-3"),
    "Nature-Based Solutions": ("NBS-1", "NBS-2", "NBS-3"),
    "Adaptation": ("A-1", "A-2", "A-3"),
}

RARE_TYPOLOGY_CODES = {"M-3", "RE-3", "NBS-3", "A-3"}

SecondaryTypologyCode = Literal[
    "M-1", "M-2", "M-3",
    "RE-1", "RE-2", "RE-3",
    "NBS-1", "NBS-2", "NBS-3",
    "A-1", "A-2", "A-3",
    "None",
]


class SecondaryTypologyResult(BaseModel):
    """Structured, schema-constrained secondary typology output."""

    typology_code: SecondaryTypologyCode = Field(
        description=(
            "Return exactly one allowed subtype code for the fixed primary "
            "category, or 'None' if the text does not explicitly justify a "
            "secondary type."
        )
    )
    typology_confidence: float = Field(
        description="Confidence in the subtype assignment from 0.0 to 1.0."
    )
    typology_evidence_quote: str = Field(
        description=(
            "A short exact quote from the policy statement or verbatim text "
            "that justifies the subtype. Return 'None' when typology_code is "
            "'None'."
        )
    )


ClimateScreenCode = Literal["explicit_self", "explicit_parent", "exclude"]
FinancialInstrumentFlag = Literal["yes", "no"]


class ClimateScreenResult(BaseModel):
    """Structured, schema-constrained climate screen output."""

    climate_screen: ClimateScreenCode = Field(
        description=(
            "Return 'explicit_self' when the row text itself explicitly states a "
            "climate objective or mechanism; 'explicit_parent' only when a sub-row "
            "clearly inherits explicit climate relevance from a climate-explicit "
            "parent; otherwise return 'exclude'."
        )
    )
    is_financial_instrument: FinancialInstrumentFlag = Field(
        description=(
            "Return 'yes' when the policy is a broad financial instrument such as "
            "a fee, carbon pricing tool, rebate, grant, subsidy, tax credit, "
            "loan, trust fund, budget appropriation, or public investment line."
        )
    )


CLIMATE_SCREEN_GUIDE = """
CLIMATE SCREENING GUIDE:

You are deciding whether ONE policy row should remain in a climate-policy dataset.
Your answer must follow the user's earlier guidance, not generic climate-policy
intuition.

Allowed outputs:
- explicit_self
- explicit_parent
- exclude

Decision rules:
1. explicit_self:
   Use when the row text itself explicitly states a climate objective,
   climate hazard, OR a recognized climate mechanism/intervention. Explicit
   examples include GHG reduction, emissions, carbon, net-zero,
   decarbonization, adaptation, resilience, heat-risk reduction,
   flood-risk reduction, smoke-risk reduction, drought response, coastal
   protection, or clearly named climate actions such as building retrofit,
   energy efficiency, renewable energy, electrification, EV charging, waste
   diversion, composting, transit expansion, modal shift, tree planting,
   green infrastructure, water conservation, regenerative agriculture, or
   ecosystem restoration. A row does NOT need to literally say "GHG" or
   "emissions" if it already names a well-established climate intervention.

2. explicit_parent:
   Use ONLY for sub-rows where:
   - the parent statement or parent action context is climate-relevant, AND
   - the child row is clearly a budget, funding, fee, financing, grant,
     support, or implementation line for that parent action.
   Do NOT use explicit_parent for generic child rows that are not obviously
   funding/support instruments.
   For budget-only sub-items under already-classified adaptation, mitigation,
   resource-efficiency, or nature-based parent actions, explicit_parent is the
   correct label even if the child row itself does not repeat climate wording.

3. exclude:
   Use when climate relevance is only indirect, inferred, speculative, or
   socially/economically generic AND the row does not name a concrete climate
   mechanism. Generic social, housing, anti-poverty, homelessness,
   economic-development, and capacity-building rows should be excluded unless
   they explicitly mention climate hazard/outcome language or a concrete
   climate intervention in the row itself.

4. Social policy exception:
   A social policy may still be climate-relevant if the row explicitly says it
   reduces heat exposure, flood risk, smoke exposure, resilience risk, or
   similar climate harms.

5. Financial instruments:
   Broad financial instruments include fees, carbon pricing, rebates, grants,
   subsidies, tax credits, loans, trust funds, budget appropriations, and
   public investment lines. But being a financial instrument alone does NOT
   justify inclusion.

Examples from prior guidance:
- Housing Trust Fund funding/capacity row with no explicit climate text
  -> exclude, yes
- Homelessness prevention / poverty reduction / Courtyard and MORE team rows
  with no explicit climate text -> exclude
- Retrofit 20% of total 5+ unit residential buildings by 2030
  -> explicit_self, no
- Divert 90% of commercial, industrial, and institutional waste by 2030
  -> explicit_self, no
- Budget-only public investment sub-item under explicit adaptation/NbS parent
  -> explicit_parent, yes
- Additional EUR 129 million / €1.4 million public-investment sub-items for a
  Line of Action in a climate plan -> explicit_parent, yes
- Direct financial assistance for carbon-related soil programs or regenerative
  agriculture -> explicit_self, yes
"""

SECONDARY_TYPOLOGY_GUIDE = """
SECONDARY CATEGORY TYPOLOGY:

You are assigning a secondary-category subtype code for ONE policy instance.
The primary category is already fixed and must NOT be changed.

Allowed subtype families:
- Mitigation primary: M-1, M-2, M-3
- Resource Efficiency primary: RE-1, RE-2, RE-3
- Nature-Based Solutions primary: NBS-1, NBS-2, NBS-3
- Adaptation primary: A-1, A-2, A-3

EVIDENCE STANDARD:
- Assign a subtype only when the policy statement or verbatim source text
  explicitly supports it.
- Use location context and mechanism context only to interpret the text, not
  as stand-alone evidence.
- If the evidence is only plausible, implied, or inferable, return 'None'.
- For rare types (M-3, RE-3, NBS-3, A-3), be conservative. If there is any
  doubt, return 'None'.

Subtype rules:

Mitigation primary:
- M-1 Efficiency-enabled Mitigation → secondary Resource Efficiency.
  Requires both an emissions-reduction commitment and a named efficiency action
  in the same policy instance.
- M-2 Resilience-integrated Mitigation → secondary Adaptation.
  Requires both an emissions-reduction commitment and a named climate
  resilience, emergency preparedness, or hazard-response measure in the same
  policy instance.
- M-3 Sequestration-based Mitigation → secondary Nature-Based Solutions.
  Requires an explicit sequestration, carbon storage, or offset goal delivered
  through a named ecological mechanism such as canopy, afforestation, wetlands,
  or urban forests.

Resource Efficiency primary:
- RE-1 Emissions-quantified Efficiency → secondary Mitigation.
  Requires an explicit GHG, carbon, net-zero, or emissions target in the same
  policy instance. Implied emissions co-benefits are NOT enough.
- RE-2 Climate-resilient Efficiency → secondary Adaptation.
  Requires a named climate hazard or scarcity condition motivating the
  efficiency action, such as drought, heat-driven peak demand, or water stress.
- RE-3 Ecosystem-supported Efficiency → secondary Nature-Based Solutions.
  Requires a named ecological intervention as the mechanism achieving the
  resource saving, such as trees reducing cooling load or bioswales reducing
  treated water demand.

Nature-Based Solutions primary:
- NBS-1 Hazard-responsive NBS → secondary Adaptation.
  Requires a named hazard such as heat, flooding, drought, storm surge,
  desertification, or dust reduction, with ecology as the means of response.
- NBS-2 Sequestration-delivering NBS → secondary Mitigation.
  Requires explicit carbon sequestration, carbon storage, emissions offset, or
  link to an emissions-reduction goal in the policy text.
- NBS-3 Resource-saving NBS → secondary Resource Efficiency.
  Requires an explicit energy, water, or treatment-capacity saving goal
  delivered through the ecological intervention.

Adaptation primary:
- A-1 Ecosystem-mediated Adaptation → secondary Nature-Based Solutions.
  Requires a named ecological mechanism such as wetland restoration,
  afforestation, bioswales, tree planting, or green belts used to reduce a
  hazard or vulnerability.
- A-2 Efficiency-supported Adaptation → secondary Resource Efficiency.
  Requires an explicit efficiency action or efficiency target embedded in the
  adaptation plan.
- A-3 Emissions-coupled Adaptation → secondary Mitigation.
  Requires an explicit decarbonization, emissions-reduction, low-carbon, or
  GHG-mitigation component in the same policy instance. Do NOT confuse
  disaster-risk mitigation with GHG mitigation.

Common errors to avoid:
- Do NOT add Mitigation to Resource Efficiency just because efficiency usually
  reduces emissions. RE-1 requires an explicit emissions target.
- Do NOT add Adaptation to every NBS policy. NBS-1 requires a named hazard in
  the policy text or source quote.
- Do NOT add NBS to Mitigation unless biological sequestration is explicitly
  named as the emissions mechanism.
- Do NOT assign a rare subtype because it seems plausible from background
  knowledge. The text must say it.
"""


def secondary_category_for_typology(code: Optional[str]) -> str:
    """Map a subtype code to the exported secondary category."""
    if not code:
        return "None"
    normalized = str(code).strip().upper()
    return SECONDARY_CODE_TO_CATEGORY.get(normalized, "None")


def normalize_typology_code(primary_category: Optional[str], code: Optional[str]) -> str:
    """Return a valid subtype code for the given primary, or 'None'."""
    if not code:
        return "None"
    normalized = str(code).strip().upper()
    allowed = PRIMARY_TO_ALLOWED_TYPOLOGY_CODES.get(primary_category or "", ())
    return normalized if normalized in allowed else "None"


# =============================================================================
# CLIMATE SCREEN CUE HELPERS
# =============================================================================

FINANCIAL_INSTRUMENT_KEYWORDS = (
    "fee",
    "fees",
    "pricing",
    "carbon pricing",
    "tax",
    "taxes",
    "levy",
    "levies",
    "rebate",
    "rebates",
    "grant",
    "grants",
    "subsidy",
    "subsidies",
    "tax credit",
    "tax credits",
    "loan",
    "loans",
    "financing",
    "financial",
    "fund",
    "funding",
    "trust fund",
    "budget",
    "budgetary",
    "appropriation",
    "appropriations",
    "public investment",
    "investment",
    "investments",
    "bond",
    "bonds",
    "cost share",
)

FINANCIAL_INSTRUMENT_TYPES = {
    "pricing-mechanism",
    "incentive-program",
}

SUPPORT_LINE_KEYWORDS = (
    "capacity building",
    "technical assistance",
    "program support",
    "implementation support",
    "administrative support",
    "public investment",
    "funding",
    "budget",
    "allocate",
    "allocation",
)

GENERIC_NON_CLIMATE_MECHANISMS = (
    "no_direct_climate_mechanism",
    "unspecified_climate_effect",
    "non_climate_economic_development",
    "poverty_reduction_goal",
    "housing_first",
    "affordable_housing_funding",
    "inclusive_community_engagement",
    "program_intervention_support",
    "displacement_prevention",
)

EXPLICIT_CLIMATE_KEYWORDS = (
    "climate",
    "greenhouse gas",
    "greenhouse gases",
    "ghg",
    "emission",
    "emissions",
    "carbon",
    "net zero",
    "decarbonization",
    "decarbonisation",
    "decarbonize",
    "decarbonise",
    "mitigation",
    "adaptation",
    "adaptive",
    "resilience",
    "resilient",
    "extreme heat",
    "heatwave",
    "heat wave",
    "heat island",
    "flood",
    "flooding",
    "drought",
    "sea level",
    "storm surge",
    "coastal erosion",
    "wildfire smoke",
    "dust storm",
    "dust storms",
    "desertification",
)

PRIMARY_MECHANISM_KEYWORDS = {
    "Mitigation": (
        "renewable",
        "renewables",
        "solar",
        "wind",
        "electrify",
        "electrification",
        "ev charging",
        "methane",
        "landfill",
        "mode shift",
        "vehicle miles traveled",
        "vmt",
    ),
    "Adaptation": (
        "cooling center",
        "cooling centers",
        "warning system",
        "warning systems",
        "preparedness",
        "risk reduction",
        "heat alert",
        "heat alerts",
        "smoke",
        "shoreline",
        "retreat",
    ),
    "Resource Efficiency": (
        "energy efficiency",
        "energy efficient",
        "water efficiency",
        "water efficient",
        "water conservation",
        "reduce energy use",
        "reduce water use",
        "reduce electricity use",
        "reduce resource use",
        "reduce energy consumption",
        "reduce water consumption",
        "recycling",
        "reuse",
        "waste diversion",
        "retrofit",
        "retrofits",
        "benchmarking",
        "retro commissioning",
        "retro-commissioning",
    ),
    "Nature-Based Solutions": (
        "tree",
        "trees",
        "tree canopy",
        "tree planting",
        "green roof",
        "green roofs",
        "wetland",
        "wetlands",
        "riparian",
        "regenerative agriculture",
        "soil conservation",
        "reforestation",
        "afforestation",
        "bioswale",
        "bioswales",
        "green infrastructure",
        "ecological corridor",
        "ecological corridors",
        "infiltration garden",
        "infiltration gardens",
        "aquifer recharge",
    ),
}


def _screen_text(*parts: Optional[str]) -> str:
    """Normalize text inputs for keyword-based screen cues."""
    text = " ".join(str(part or "") for part in parts).lower()
    text = re.sub(r"[^a-z0-9€$%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    padded = f" {text} "
    hits = []
    for keyword in keywords:
        normalized = _screen_text(keyword)
        if normalized and f" {normalized} " in padded:
            hits.append(keyword)
    return tuple(dict.fromkeys(hits))


def _climate_keyword_hits(
    text: str,
    *,
    primary_category: Optional[str] = None,
) -> tuple[str, ...]:
    if not text:
        return ()
    hits = list(_keyword_hits(text, EXPLICIT_CLIMATE_KEYWORDS))
    for mechanism_keywords in PRIMARY_MECHANISM_KEYWORDS.values():
        hits.extend(_keyword_hits(text, mechanism_keywords))
    return tuple(dict.fromkeys(hits))


def _cue_summary(hits: tuple[str, ...]) -> str:
    return ", ".join(hits) if hits else "none"


def _is_broad_financial_instrument(text: str, instrument_type: Optional[str]) -> bool:
    if (instrument_type or "").strip() in FINANCIAL_INSTRUMENT_TYPES:
        return True
    if (instrument_type or "").strip() == "infrastructure-investment":
        return bool(_keyword_hits(text, FINANCIAL_INSTRUMENT_KEYWORDS))
    return bool(_keyword_hits(text, FINANCIAL_INSTRUMENT_KEYWORDS))


def _is_support_line(text: str) -> bool:
    return bool(_keyword_hits(text, SUPPORT_LINE_KEYWORDS))


def _mechanism_forces_exclusion(canonical_mechanism: Optional[str]) -> bool:
    normalized = (canonical_mechanism or "").strip().lower()
    return any(flag in normalized for flag in GENERIC_NON_CLIMATE_MECHANISMS)


# =============================================================================
# STAGE 1: MECHANISM EXTRACTION
# =============================================================================

class MechanismExtractionSignature(dspy.Signature):
    """Extract the canonical causal mechanism from a climate policy.

    The goal is to produce a NORMALIZED mechanism string that will be identical
    for structurally equivalent policies regardless of city, scale, or wording.

    NORMALIZATION RULES:

    1. Strip location-specific details (city names, building counts, acreage)
    2. Strip numeric targets (percentages, unit counts, MW, acres)
    3. Strip deadlines (by 20XX)
    4. Keep the CAUSAL CHAIN: what input changes → what output changes
    5. Keep the SECTOR: transport, buildings, waste, energy, land use, water
    6. Keep the INSTRUMENT CLASS: mandate, target, incentive, code, program

    FORMAT: '<action> → <climate_effect>' in snake_case

    CONSISTENCY PRINCIPLE: Two policies that work through the same physical
    causal chain must produce the same canonical_mechanism string regardless
    of how they are worded, what city they are in, or what numeric targets
    they set.

    EXAMPLES (grouped by equivalence class):

    Waste diversion from landfill (any stream — commercial, residential, C&D):
    → "waste_diversion → landfill_methane_avoidance"

    Fleet/vehicle electrification (any fleet — municipal, transit, community):
    → "fleet_electrification → transport_emissions_reduction"

    Urban tree planting / canopy targets (any city, any target %):
    → "urban_tree_canopy_expansion → heat_mitigation_and_carbon_sequestration"

    Residential building energy retrofits (any scale or target):
    → "residential_building_retrofit → energy_use_reduction"

    Commercial/industrial building energy retrofits:
    → "commercial_building_retrofit → energy_use_reduction"

    Solar PV deployment (on government or private buildings):
    → "solar_pv_deployment → fossil_generation_displacement"

    Building operational optimization (tune-ups, retro-commissioning):
    → "building_retuning → energy_use_reduction"

    Energy benchmarking and disclosure (reporting/transparency programs):
    → "energy_benchmarking_disclosure → market_signal_for_efficiency"

    Water efficiency standards / per-capita water reduction:
    → "water_efficiency_standards → per_capita_water_reduction"

    Modal shift to sustainable transport (transit, cycling, walking):
    → "modal_shift_to_sustainable_transport → transport_emissions_reduction"

    EV charging infrastructure deployment:
    → "ev_charging_infrastructure → ev_adoption_enabling"

    Economy-wide GHG reduction / net-zero targets:
    → "economy_wide_ghg_target → emissions_reduction_pathway"

    Building energy codes / minimum performance standards:
    → "building_energy_code → energy_use_reduction"

    Organic waste composting:
    → "organic_waste_composting → landfill_methane_avoidance_and_nutrient_recovery"

    Refrigerant management (capture, destruction, leak prevention):
    → "refrigerant_management → fugitive_emissions_reduction"

    Embodied carbon reduction in construction materials:
    → "embodied_carbon_reduction → construction_emissions_reduction"

    Farmland/natural land protection and conservation:
    → "natural_land_protection → ecosystem_and_carbon_preservation"

    Regenerative agriculture programs:
    → "regenerative_agriculture → soil_carbon_and_ecosystem_restoration"

    Afforestation / green belt / shelterbelt programs:
    → "afforestation_greenbelt → desertification_and_erosion_control"

    Desalination capacity expansion:
    → "desalination_capacity_expansion → water_supply_resilience"

    Environmental monitoring / data systems:
    → "environmental_monitoring_system → adaptation_planning_support"

    Occupational heat safety restrictions:
    → "occupational_heat_restriction → heat_exposure_reduction"

    Wastewater treatment infrastructure upgrades:
    → "wastewater_treatment_upgrade → sanitation_improvement"

    International development finance / aid commitments:
    → "international_development_finance → development_capacity_building"

    Renewable energy portfolio / clean energy targets (utility-scale):
    → "renewable_energy_target → grid_decarbonization"

    Energy storage deployment:
    → "energy_storage_deployment → grid_flexibility_and_renewable_integration"

    Transit-oriented development / housing near transit corridors:
    → "transit_oriented_development → transport_emissions_and_land_use_reduction"

    Public transit ridership / service expansion:
    → "transit_service_expansion → transport_emissions_reduction"

    Shared micromobility expansion (bike-share, scooter-share):
    → "shared_micromobility_expansion → transport_emissions_reduction"

    Cogeneration / waste-to-energy at treatment plants:
    → "cogeneration_waste_to_energy → fossil_generation_displacement_and_methane_capture"

    Waste-to-energy conversion (non-recycled waste):
    → "waste_to_energy_conversion → landfill_methane_avoidance_and_energy_recovery"

    Park system expansion (for climate/resilience purposes):
    → "park_system_expansion → heat_and_stormwater_resilience"

    Adaptation planning frameworks (national/municipal plans):
    → "adaptation_planning_framework → institutional_adaptive_capacity"

    Building performance ordinance (mandatory benchmarking + retuning):
    → "building_performance_ordinance → energy_use_reduction"

    DISAMBIGUATION RULES:

    - Building RETROFIT (physical upgrades to envelope/systems) vs building
      RETUNING (operational optimization of existing systems) vs building
      BENCHMARKING (information/reporting only) are THREE DIFFERENT mechanisms
      even though all target building energy use. The causal chains differ:
      retrofit is direct physical change, retuning is operational optimization,
      benchmarking is information provision.

    - Government fleet electrification vs communitywide vehicle electrification
      share the same mechanism (fleet_electrification) unless the communitywide
      policy is purely a market adoption target with no direct procurement.

    - Solar on government property vs solar on private property: same mechanism
      (solar_pv_deployment). Instrument type differences are captured in Stage 3.

    - A policy that COMBINES benchmarking AND mandatory retuning in a single
      ordinance should be classified by its most direct intervention
      (building_performance_ordinance → energy_use_reduction).
    """

    policy_statement: str = dspy.InputField(
        desc="The climate policy statement"
    )
    verbatim_text: str = dspy.InputField(
        desc="Original verbatim text from source document"
    )

    canonical_mechanism: str = dspy.OutputField(
        desc=(
            "Normalized mechanism string using format: "
            "'<action> → <climate_effect>'. "
            "Use snake_case. Refer to the EXAMPLES and DISAMBIGUATION RULES."
        )
    )

    sector: str = dspy.OutputField(
        desc=(
            "Policy sector. One of: 'energy_supply', 'buildings', 'transport', "
            "'waste', 'water', 'land_use', 'industry', 'cross_sector', 'governance'"
        )
    )

    mechanism_description: str = dspy.OutputField(
        desc="One-sentence plain English description of the causal chain"
    )


# =============================================================================
# STAGE 2: MECHANISM-LEVEL CLASSIFICATION
# =============================================================================

class MechanismClassificationSignature(dspy.Signature):
    f"""Classify a canonical causal mechanism (NOT an individual policy).

    You are classifying the MECHANISM, not a specific policy instance. The labels
    you assign will apply to ALL policies that share this mechanism, regardless
    of which city they come from or what numeric targets they set.

    Ask: "For ANY policy that works through this mechanism, what is the correct
    classification?"

    {CATEGORY_DEFINITIONS}

    ==================================================================================
    CLASSIFICATION RULES:
    ==================================================================================

    Rule 1 — Dominant causal pathway determines primary type

        The primary category must follow the DOMINANT causal pathway — the
        mechanism through which the LARGEST climate-relevant impact occurs.

        Do NOT default to Resource Efficiency simply because a "same service,
        less input" framing is technically possible. Instead, ask:

            "What is the single biggest climate-relevant effect of this mechanism?"

        Decision hierarchy:

        a) If the mechanism's largest quantitative climate impact flows through
           EMISSIONS REDUCTION or CARBON SINK ENHANCEMENT → primary is Mitigation,
           even if an efficiency framing could also apply.

           Mitigation wins over Resource Efficiency when:
           - The dominant physical effect is avoided GHG emissions
             (e.g., methane avoidance, eliminated combustion, displaced fossil generation)
           - The dominant physical effect is reduced transport emissions via mode shift
           - The policy's stated framing emphasizes emissions or decarbonization

        b) If the mechanism's primary objective and measurable outcome is
           delivering the same service with less resource input, AND emissions
           reduction is a secondary consequence → primary is Resource Efficiency.

           Resource Efficiency wins when:
           - The main point is input optimization (kWh/m², gallons/person, kg/unit)
           - The policy would be justified purely on cost/efficiency grounds
           - Emissions reduction follows as a consequence, not as the goal

        c) If the mechanism explicitly addresses climate hazards, vulnerability,
           or resilience → primary is Adaptation.

        d) If the mechanism fundamentally depends on living ecosystems
           → primary is Nature-Based Solutions.

    Rule 2 — Secondary categories are NOT decided here

        This stage exists to lock the primary category consistently across all
        policies sharing the same mechanism. The expert secondary typology is
        assigned later at the policy-text level using explicit textual evidence.

        Do NOT try to infer or bake in secondary categories at the mechanism
        level. Focus on the dominant causal pathway and the correct primary.

    Rule 3 — Climate system vs climate impacts
        - Acts on emissions/carbon cycle → Mitigation
        - Acts on hazards/vulnerability → Adaptation

    Rule 4 — Natural vs engineered pathways
        - Ecosystem-mediated → NbS
        - Technological/infrastructural → other categories

    {EDGE_CASES_REFERENCE}
    """

    # ---- Inputs ----
    canonical_mechanism: str = dspy.InputField(
        desc="The canonical mechanism string (e.g., 'waste_diversion → landfill_methane_avoidance')"
    )
    sector: str = dspy.InputField(
        desc="Policy sector"
    )
    mechanism_description: str = dspy.InputField(
        desc="Plain English description of the causal chain"
    )
    representative_policies: str = dspy.InputField(
        desc=(
            "JSON list of 1-3 representative policy statements that share this "
            "mechanism, from different cities/contexts if available"
        )
    )

    # ---- Outputs ----
    dominant_pathway_test: str = dspy.OutputField(
        desc=(
            "Answer: 'What is the single biggest climate-relevant effect of ANY "
            "policy using this mechanism?' in one sentence. This determines "
            "whether the primary category is correct per Rule 1."
        )
    )

    primary_category: str = dspy.OutputField(
        desc=(
            "Primary category for ALL policies sharing this mechanism. One of: "
            "'Mitigation', 'Adaptation', 'Resource Efficiency', "
            "'Nature-Based Solutions'. Must follow the dominant causal pathway."
        )
    )

    primary_causal_pathway: str = dspy.OutputField(
        desc=(
            "The mechanism's primary causal pathway using arrow notation. "
            "Examples: "
            "'Human activity → ↓ GHG emissions → ↓ atmospheric forcing', "
            "'Same service → ↓ energy input', "
            "'Climate hazard → ↓ exposure', "
            "'Ecosystem enhancement → ↑ carbon sinks + ↓ heat exposure'"
        )
    )

    causal_mechanism_detail: str = dspy.OutputField(
        desc=(
            "Detailed description: (1) What human activity or system is changed, "
            "(2) What physical/ecological process mediates the effect, "
            "(3) What the climate-relevant outcome is."
        )
    )

    typical_policy_instruments: str = dspy.OutputField(
        desc=(
            "List typical policy instruments that implement this mechanism "
            "(e.g., 'mandatory targets, landfill bans, pricing mechanisms, "
            "infrastructure investment'). Used as reference in Stage 3."
        )
    )

    classification_reasoning: str = dspy.OutputField(
        desc=(
            "Step-by-step reasoning: (1) Dominant causal pathway, "
            "(2) Why this primary over alternatives, (3) Edge cases applied, "
            "(4) Confirm the primary applies to ALL instances sharing this "
            "mechanism, and (5) explicitly note that secondary subtype "
            "assignment is deferred to policy-level evidence."
        )
    )

    confidence_score: float = dspy.OutputField(
        desc="Confidence in classification (0.0 to 1.0)"
    )

    edge_case_notes: str = dspy.OutputField(
        desc="Any edge case considerations, or 'None' if straightforward."
    )


# =============================================================================
# STAGE 3: POLICY-LEVEL ENRICHMENT
# =============================================================================

class SecondaryTypologySignature(dspy.Signature):
    f"""Assign the expert secondary-category subtype code for ONE policy.

    The primary category is already fixed. Your job is to decide whether this
    specific policy instance qualifies for one expert secondary subtype code
    based on EXPLICIT textual evidence in the policy statement or verbatim text.

    {SECONDARY_TYPOLOGY_GUIDE}
    """

    primary_category: str = dspy.InputField(
        desc="Already-assigned primary category. Must not be changed."
    )
    policy_statement: str = dspy.InputField(
        desc="The specific policy statement."
    )
    verbatim_text: str = dspy.InputField(
        desc="Original source text used as the main evidence source."
    )
    canonical_mechanism: str = dspy.InputField(
        desc="Canonical mechanism for context only, not stand-alone evidence."
    )
    mechanism_description: str = dspy.InputField(
        desc="Plain-English causal mechanism description for context only."
    )
    location_vulnerability_context: str = dspy.InputField(
        desc=(
            "Location vulnerability context for disambiguation only. "
            "Do NOT use this as stand-alone evidence for assigning a subtype."
        ),
        default="No vulnerability context provided"
    )

    secondary_typology: SecondaryTypologyResult = dspy.OutputField(
        desc=(
            "Structured secondary typology result. typology_code must be "
            "returned verbatim as one of the allowed subtype literals or 'None'."
        )
    )


class ClimateScreenSignature(dspy.Signature):
    f"""Assign the compact climate screen for ONE policy row.

    The purpose is to decide whether the policy should remain in the climate
    dataset based on explicit evidence, while following the user's earlier
    screening guidance for social, financial, and budget-only rows.

    {CLIMATE_SCREEN_GUIDE}
    """

    policy_statement: str = dspy.InputField(
        desc="The specific row text that must carry explicit climate evidence for explicit_self."
    )
    verbatim_text: str = dspy.InputField(
        desc="Original source text for the same row."
    )
    parent_statement: str = dspy.InputField(
        desc="Parent statement for sub-rows. Use only to decide explicit_parent inheritance."
    )
    role: str = dspy.InputField(
        desc="Row role, typically individual, parent, or sub."
    )
    primary_category: str = dspy.InputField(
        desc="Existing primary category for context only."
    )
    canonical_mechanism: str = dspy.InputField(
        desc="Canonical mechanism for context only."
    )
    mechanism_description: str = dspy.InputField(
        desc="Mechanism description for context only. Do not use it as a substitute for missing row evidence."
    )
    instrument_type: str = dspy.InputField(
        desc="Existing instrument type hint from enrichment."
    )
    climate_relevance_hint: str = dspy.InputField(
        desc="Existing Stage 3 climate relevance hint."
    )
    climate_keyword_cues: str = dspy.InputField(
        desc="Compact keyword hits that may indicate explicit climate language in the row."
    )
    parent_climate_keyword_cues: str = dspy.InputField(
        desc="Compact keyword hits that may indicate explicit climate language in the parent."
    )
    finance_keyword_cues: str = dspy.InputField(
        desc="Compact keyword hits that may indicate a broad financial instrument."
    )
    support_keyword_cues: str = dspy.InputField(
        desc="Compact keyword hits that may indicate a support or budget line."
    )
    deterministic_finance_hint: str = dspy.InputField(
        desc="Deterministic yes/no hint for broad financial instrument status."
    )
    mechanism_exclusion_hint: str = dspy.InputField(
        desc="Deterministic yes/no hint when the canonical mechanism is explicitly non-climate."
    )

    climate_screen_result: ClimateScreenResult = dspy.OutputField(
        desc="Structured climate screen result with climate_screen and is_financial_instrument."
    )


class PolicyEnrichmentSignature(dspy.Signature):
    f"""Enrich an individual policy with instance-specific metadata.

    The primary category has ALREADY been determined at the mechanism level.
    The expert secondary subtype is determined separately from policy text.
    Do NOT override either one. Your job here is to add:

    1. Instrument type and directness for THIS specific policy
    2. Climate relevance for THIS specific policy
    3. Key textual indicators from the source text
    4. Co-benefits specific to this instance

    {CATEGORY_DEFINITIONS}

    ==================================================================================
    RULES FOR THIS STAGE:
    ==================================================================================

    Rule A — Location vulnerability context informs interpretation only

        You may use location vulnerability context to assess climate relevance
        and to recognize co-benefits, but it does NOT create an expert
        secondary subtype by itself.

    Rule B — Instrument type classification

        Classify the specific policy instrument based on the source text:

        'regulatory-mandate'        — Binding law, code, or ordinance requiring
                                      compliance
        'performance-standard'      — Required measurable outcome levels
        'information-disclosure'    — Benchmarking, reporting, or transparency
                                      requirements
        'incentive-program'         — Subsidies, rebates, tax credits, density
                                      bonuses, or other voluntary inducements
        'target-setting'            — Stated goals without binding enforcement
        'infrastructure-investment' — Capital deployment for physical assets
        'procurement-requirement'   — Government purchasing or construction rules
        'planning-strategy'         — Plans, roadmaps, maps, or assessments
                                      without binding actions
        'pricing-mechanism'         — Taxes, fees, tariffs, or pricing reforms
                                      designed to change behavior

    Rule C — Instrument directness

        'direct'   — The policy mechanically produces the outcome with no
                     intermediate voluntary step. The regulated party must
                     comply and compliance itself delivers the benefit.

        'enabling' — The policy creates conditions for the outcome but depends
                     on downstream voluntary action. Information provision,
                     infrastructure availability, and incentive programs are
                     typically enabling — they lower barriers but don't
                     guarantee the outcome.

        'indirect' — The policy's climate effect is a secondary consequence
                     of a non-climate primary objective. The policy was not
                     designed for climate purposes but happens to produce
                     climate benefits.

    Rule D — Climate relevance threshold

        'direct'     — Climate is the primary stated objective. The policy
                       explicitly targets GHG reduction, climate adaptation,
                       or climate resilience.

        'indirect'   — Climate benefit is significant but secondary. The policy
                       would likely exist without climate concerns, but its
                       climate impact is substantial and recognized.

        'peripheral' — Climate link is distant or speculative. The policy
                       would exist entirely without climate concerns.

    {EDGE_CASES_REFERENCE}
    """

    # ---- Inputs ----
    policy_statement: str = dspy.InputField(
        desc="The specific policy statement"
    )
    verbatim_text: str = dspy.InputField(
        desc="Original source text"
    )

    # Pre-filled from mechanism registry (Stage 2)
    assigned_primary: str = dspy.InputField(
        desc="Primary category from mechanism level — do NOT change"
    )
    assigned_causal_pathway: str = dspy.InputField(
        desc="Causal pathway from Stage 2 — for reference"
    )
    assigned_causal_detail: str = dspy.InputField(
        desc="Detailed causal mechanism from Stage 2 — for reference"
    )
    assigned_typical_instruments: str = dspy.InputField(
        desc="Typical instruments for this mechanism from Stage 2 — for reference"
    )

    location_vulnerability_context: str = dspy.InputField(
        desc=(
            "Known climate vulnerabilities for this location. "
            "Format: '<location> faces: <hazard1>, <hazard2>'. "
            "Use to assess additional secondary categories per Rule A."
        ),
        default="No vulnerability context provided"
    )

    # ---- Outputs ----
    instrument_type: str = dspy.OutputField(
        desc=(
            "Policy instrument type per Rule B. One of: "
            "'regulatory-mandate', 'performance-standard', "
            "'information-disclosure', 'incentive-program', 'target-setting', "
            "'infrastructure-investment', 'planning-strategy', "
            "'procurement-requirement', 'pricing-mechanism'"
        )
    )

    instrument_directness: str = dspy.OutputField(
        desc="Per Rule C. One of: 'direct', 'enabling', 'indirect'"
    )

    climate_relevance: str = dspy.OutputField(
        desc="Per Rule D. One of: 'direct', 'indirect', 'peripheral'"
    )

    key_indicators: str = dspy.OutputField(
        desc="Specific words/phrases from the source text that signal classification"
    )

    co_benefits: str = dspy.OutputField(
        desc=(
            "Secondary benefits considered but not meeting the threshold for "
            "a secondary category. Set to 'None' if not applicable."
        )
    )

    edge_case_notes: str = dspy.OutputField(
        desc="Instance-specific ambiguities, or 'None' if straightforward."
    )


# =============================================================================
# PIPELINE ORCHESTRATION
# =============================================================================

class ConsistentPolicyClassifier(dspy.Module):
    """
    Three-stage pipeline:

    Stage 1: Extract canonical mechanisms from all policies
    Stage 2: Classify each unique mechanism ONCE for PRIMARY category
             consistency across all matching policies
    Stage 3: Enrich each policy with instance-specific metadata
             and assign the expert secondary subtype from policy text
             (instrument type, directness, climate relevance,
             co-benefits, guide-native secondary typing)

    Consistency guarantee: Every policy sharing the same mechanism gets the
    same PRIMARY category. Secondary categories are assigned per policy using
    explicit textual evidence so they do not get over-propagated from the
    mechanism registry.
    """

    def __init__(self):
        super().__init__()
        self.extract_mechanism = dspy.ChainOfThought(MechanismExtractionSignature)
        self.classify_mechanism = dspy.ChainOfThought(MechanismClassificationSignature)
        self.classify_secondary_typology = dspy.ChainOfThought(SecondaryTypologySignature)
        self.screen_climate_policy = dspy.ChainOfThought(ClimateScreenSignature)
        self.enrich_policy = dspy.ChainOfThought(PolicyEnrichmentSignature)

        # The registry: mechanism_key → classification dict
        self.mechanism_registry: Dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # Mechanism string normalization (Fix #8 from critique)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_mechanism_key(s: str) -> str:
        """Lowercase, collapse whitespace/underscores, normalize arrow notation."""
        s = s.strip().lower()
        # Normalize arrow variants: →, ->, ➜, etc.
        s = re.sub(r"\s*(?:→|->|➜|➡)\s*", " → ", s)
        # Collapse whitespace and underscores to single underscore
        s = re.sub(r"[\s_]+", "_", s)
        return s

    @staticmethod
    def _mechanism_similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def canonicalize_mechanisms(
        self,
        policies: List[dict],
        threshold: float = 0.85,
    ) -> List[dict]:
        """Cluster near-identical mechanism strings to a single canonical form.

        This prevents variants like 'fleet_electrification → transport_emissions_reduction'
        and 'fleet_electrification → transport_emission_reduction' from being classified
        independently. Uses greedy single-linkage clustering on normalized strings.
        """
        # Build unique mechanism set
        raw_mechanisms: Dict[str, str] = {}  # normalized -> first-seen raw
        for p in policies:
            raw = p.get("canonical_mechanism", "")
            norm = self._normalize_mechanism_key(raw)
            if norm not in raw_mechanisms:
                raw_mechanisms[norm] = raw

        # Greedy clustering: assign each mechanism to the first cluster it matches
        clusters: Dict[str, str] = {}  # normalized -> canonical representative
        canonical_list: List[str] = []

        for norm in raw_mechanisms:
            matched = False
            for canon in canonical_list:
                if self._mechanism_similarity(norm, canon) >= threshold:
                    clusters[norm] = canon
                    matched = True
                    break
            if not matched:
                clusters[norm] = norm
                canonical_list.append(norm)

        # Count merges
        merged = sum(1 for k, v in clusters.items() if k != v)
        if merged:
            print(f"  [mechanism-cluster] merged {merged} variant mechanism strings "
                  f"into {len(canonical_list)} canonical forms")

        # Rewrite policies in-place with the canonical normalized form
        # This ensures stage2_classify_mechanisms groups correctly since
        # the registry key = the mechanism string on the policy dict.
        for p in policies:
            raw = p.get("canonical_mechanism", "")
            norm = self._normalize_mechanism_key(raw)
            canon_norm = clusters.get(norm, norm)
            # Store the normalized canonical form so registry keys are consistent
            p["canonical_mechanism"] = raw_mechanisms.get(canon_norm, raw)
            # Also store the normalized key for registry lookups
            p["_mechanism_key"] = canon_norm

        return policies

    def stage2_classify_mechanisms(self, policies: List[dict]) -> Dict[str, dict]:
        """Group by normalized mechanism key, classify each unique mechanism once.

        Uses ``_mechanism_key`` (set by ``canonicalize_mechanisms``) so the
        registry is keyed by a serialization-safe normalized string.  Falls
        back to ``canonical_mechanism`` if ``_mechanism_key`` is absent.

        Picks up to 3 representative policies from different locations so the
        model sees cross-city context when deciding the mechanism's labels.
        """
        mechanism_groups: Dict[str, list] = defaultdict(list)
        for policy in policies:
            key = policy.get("_mechanism_key", policy["canonical_mechanism"])
            mechanism_groups[key].append(policy)

        registry: Dict[str, dict] = {}

        for mechanism_key, group in mechanism_groups.items():
            # Pick up to 3 representatives from different locations
            seen_locations: set = set()
            representatives: list = []
            for p in group:
                loc = p.get("location", p.get("__city_key", "unknown"))
                if loc not in seen_locations and len(representatives) < 3:
                    representatives.append({
                        "location": loc,
                        "policy_statement": p["policy_statement"],
                    })
                    seen_locations.add(loc)

            classification = self.classify_mechanism(
                canonical_mechanism=mechanism_key,
                sector=group[0].get("sector", "cross_sector"),
                mechanism_description=group[0].get("mechanism_description", ""),
                representative_policies=json.dumps(representatives),
            )

            registry[mechanism_key] = {
                "primary_category":          classification.primary_category,
                "secondary_categories":      "None",
                "primary_causal_pathway":    classification.primary_causal_pathway,
                "causal_mechanism_detail":   classification.causal_mechanism_detail,
                "typical_policy_instruments": classification.typical_policy_instruments,
                "dominant_pathway_test":     classification.dominant_pathway_test,
                "classification_reasoning":  classification.classification_reasoning,
                "confidence_score":          classification.confidence_score,
                "edge_case_notes":           classification.edge_case_notes,
                "policy_count":              len(group),
                "locations":                 list(seen_locations),
            }

        self.mechanism_registry = registry
        return registry

    def classify_policy_secondary(
        self,
        *,
        primary_category: str,
        policy_statement: str,
        verbatim_text: str,
        canonical_mechanism: str,
        mechanism_description: str,
        location_vulnerability_context: str = "No vulnerability context provided",
    ) -> dict:
        """Assign and normalize the expert secondary subtype for one policy."""
        prediction = self.classify_secondary_typology(
            primary_category=primary_category,
            policy_statement=policy_statement,
            verbatim_text=verbatim_text,
            canonical_mechanism=canonical_mechanism,
            mechanism_description=mechanism_description,
            location_vulnerability_context=location_vulnerability_context,
        )

        structured = getattr(prediction, "secondary_typology", None)
        raw_code = getattr(structured, "typology_code", getattr(prediction, "typology_code", "None"))
        code = normalize_typology_code(primary_category, raw_code)
        try:
            confidence = float(
                getattr(structured, "typology_confidence", getattr(prediction, "typology_confidence", 0.0))
            )
        except (TypeError, ValueError):
            confidence = 0.0
        evidence_quote = (
            getattr(structured, "typology_evidence_quote", getattr(prediction, "typology_evidence_quote", "None"))
            or "None"
        )

        if str(evidence_quote).strip().lower() in ("", "none", "null"):
            code = "None"
            evidence_quote = "None"

        if code in RARE_TYPOLOGY_CODES and confidence < 0.85:
            code = "None"
            evidence_quote = "None"
            confidence = min(confidence, 0.84)

        if code == "None":
            evidence_quote = "None"
            confidence = 0.0

        return {
            "typology_code": code,
            "typology_confidence": confidence,
            "typology_evidence_quote": evidence_quote,
            "secondary_categories": secondary_category_for_typology(code),
            "classification_schema_version": CLASSIFICATION_SCHEMA_VERSION,
            "secondary_profile": SECONDARY_PROFILE,
        }

    @staticmethod
    def default_climate_screen(
        *,
        row_text: str,
        role: Optional[str],
        parent_explicit: bool,
        instrument_type: Optional[str],
        canonical_mechanism: Optional[str],
    ) -> dict:
        """Conservative fallback used if the LM screen fails."""
        is_financial = _is_broad_financial_instrument(row_text, instrument_type)
        mechanism_is_climate = bool(
            canonical_mechanism
            and canonical_mechanism.strip().lower() not in ("", "unknown")
            and not _mechanism_forces_exclusion(canonical_mechanism)
        )
        if (
            (role or "").strip() == "sub"
            and parent_explicit
            and (is_financial or _is_support_line(row_text) or mechanism_is_climate)
        ):
            climate_screen = "explicit_parent"
        elif mechanism_is_climate:
            climate_screen = "explicit_self"
        else:
            climate_screen = "exclude"
        return {
            "climate_screen": climate_screen,
            "is_financial_instrument": "yes" if is_financial else "no",
        }

    def conservative_climate_screen(
        self,
        *,
        policy_statement: str,
        verbatim_text: str,
        parent_statement: Optional[str] = None,
        role: Optional[str] = None,
        canonical_mechanism: Optional[str] = None,
        primary_category: Optional[str] = None,
        instrument_type: Optional[str] = None,
    ) -> dict:
        """Return the conservative non-LM fallback climate screen."""
        row_text = _screen_text(policy_statement, verbatim_text)
        parent_text = _screen_text(parent_statement)
        parent_explicit = bool(_climate_keyword_hits(parent_text, primary_category=primary_category))
        return self.default_climate_screen(
            row_text=row_text,
            role=role,
            parent_explicit=parent_explicit,
            instrument_type=instrument_type,
            canonical_mechanism=canonical_mechanism,
        )

    def classify_climate_screen(
        self,
        *,
        policy_statement: str,
        verbatim_text: str,
        parent_statement: Optional[str] = None,
        role: Optional[str] = None,
        canonical_mechanism: Optional[str] = None,
        mechanism_description: Optional[str] = None,
        primary_category: Optional[str] = None,
        instrument_type: Optional[str] = None,
        climate_relevance_hint: Optional[str] = None,
    ) -> dict:
        """Classify the compact climate screen using keyword cues + a narrow LM."""
        row_text = _screen_text(policy_statement, verbatim_text)
        parent_text = _screen_text(parent_statement)
        climate_hits = _climate_keyword_hits(row_text, primary_category=primary_category)
        parent_climate_hits = _climate_keyword_hits(parent_text, primary_category=primary_category)
        finance_hits = _keyword_hits(row_text, FINANCIAL_INSTRUMENT_KEYWORDS)
        support_hits = _keyword_hits(row_text, SUPPORT_LINE_KEYWORDS)
        default = self.default_climate_screen(
            row_text=row_text,
            role=role,
            parent_explicit=bool(parent_climate_hits),
            instrument_type=instrument_type,
            canonical_mechanism=canonical_mechanism,
        )

        try:
            prediction = self.screen_climate_policy(
                policy_statement=policy_statement,
                verbatim_text=verbatim_text,
                parent_statement=parent_statement or "None",
                role=role or "individual",
                primary_category=primary_category or "Unknown",
                canonical_mechanism=canonical_mechanism or "Unknown",
                mechanism_description=mechanism_description or "Unknown",
                instrument_type=instrument_type or "Unknown",
                climate_relevance_hint=climate_relevance_hint or "Unknown",
                climate_keyword_cues=_cue_summary(climate_hits),
                parent_climate_keyword_cues=_cue_summary(parent_climate_hits),
                finance_keyword_cues=_cue_summary(finance_hits),
                support_keyword_cues=_cue_summary(support_hits),
                deterministic_finance_hint=default["is_financial_instrument"],
                mechanism_exclusion_hint="yes" if _mechanism_forces_exclusion(canonical_mechanism) else "no",
            )
            structured = getattr(prediction, "climate_screen_result", None)
            climate_screen = getattr(structured, "climate_screen", getattr(prediction, "climate_screen", "exclude"))
            is_financial = getattr(
                structured,
                "is_financial_instrument",
                getattr(prediction, "is_financial_instrument", default["is_financial_instrument"]),
            )
        except Exception:
            return default

        if climate_screen not in ("explicit_self", "explicit_parent", "exclude"):
            climate_screen = "exclude"
        if is_financial not in ("yes", "no"):
            is_financial = default["is_financial_instrument"]

        budget_parent_hint = (
            "budget_line_for_parent_action" in (canonical_mechanism or "").lower()
            or "parent climate action" in (mechanism_description or "").lower()
        )
        if (
            climate_screen == "exclude"
            and (role or "").strip() == "sub"
            and is_financial == "yes"
            and budget_parent_hint
        ):
            climate_screen = "explicit_parent"
        if (
            climate_screen == "exclude"
            and canonical_mechanism
            and canonical_mechanism.strip().lower() not in ("", "unknown")
            and not _mechanism_forces_exclusion(canonical_mechanism)
        ):
            climate_screen = "explicit_self"

        return {
            "climate_screen": climate_screen,
            "is_financial_instrument": is_financial,
        }


# =============================================================================
# LOCATION VULNERABILITY LOOKUP
# =============================================================================
# Populate with known climate vulnerabilities per city key.
# Used in Stage 3 to trigger location-based Adaptation secondaries.
# Format value: "<City> faces: <hazard1>, <hazard2>, ..."
#
# City keys match the output of city_key() in the pipeline:
#   Chicago, Seattle, Las_Vegas, Miami_Dade, Austin,
#   Dakar, Kuwait, Portugal, Geneva, Hiroshima

LOCATION_VULNERABILITIES: Dict[str, str] = {
    "Chicago": (
        "Chicago faces: extreme heat events, urban heat island effect, "
        "inland flooding from intense precipitation, Great Lakes water level variability"
    ),
    "Seattle": (
        "Seattle faces: wildfire smoke exposure, landslides from heavy rainfall, "
        "urban flooding, sea-level rise in Puget Sound, drought stress on water supply"
    ),
    "Las_Vegas": (
        "Las Vegas faces: extreme heat, prolonged drought, water scarcity "
        "(Colorado River depletion), flash flooding, dust storms"
    ),
    "Miami_Dade": (
        "Miami-Dade faces: sea-level rise, tidal flooding, saltwater intrusion "
        "into freshwater aquifer, extreme heat, hurricane intensification, storm surge"
    ),
    "Austin": (
        "Austin faces: extreme heat, flash flooding, prolonged drought, "
        "wildfire risk at urban-wildland interface, water supply stress"
    ),
    "Dakar": (
        "Dakar faces: sea-level rise, coastal erosion, flooding from intense "
        "rainfall, drought, water scarcity, extreme heat, desertification pressure"
    ),
    "Kuwait": (
        "Kuwait faces: extreme heat (50°C+ events), water scarcity (near-total "
        "desalination dependence), dust storms, sea-level rise, coral bleaching"
    ),
    "Portugal": (
        "Portugal faces: wildfire risk, drought and desertification (southern regions), "
        "extreme heat events, coastal erosion, water scarcity, flooding"
    ),
    "Geneva": (
        "Geneva faces: heat waves, Alpine glacial melt affecting water supply, "
        "flooding from intense precipitation, reduced snowpack"
    ),
    "Hiroshima": (
        "Hiroshima faces: typhoon intensification, flooding and landslides from "
        "heavy rainfall, extreme heat events, sea-level rise in Seto Inland Sea"
    ),
}


def build_vulnerability_context(city_key: str) -> str:
    """Return the location vulnerability string for a given city key, or a
    fallback message if no entry exists yet.

    Normalizes the key (strips, replaces hyphens/spaces with underscores)
    to prevent silent lookup failures from formatting differences.
    """
    normalized = city_key.strip().replace("-", "_").replace(" ", "_")
    # Case-insensitive lookup: try exact, then title-case
    if normalized in LOCATION_VULNERABILITIES:
        return LOCATION_VULNERABILITIES[normalized]
    # Try title-casing each segment: "miami_dade" → "Miami_Dade"
    title = "_".join(part.capitalize() for part in normalized.split("_"))
    return LOCATION_VULNERABILITIES.get(
        title,
        "No vulnerability context provided"
    )
