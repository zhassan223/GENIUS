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
from typing import List, Dict, Optional


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

    Rule 2 — Secondary categories require INHERENT causal justification

        For each secondary category, the mechanism must have a plausible causal
        chain to that outcome that is INHERENT to the mechanism itself — meaning
        it applies to EVERY instance regardless of location or implementation.

        A secondary is inherent when:
        - The mechanism unavoidably produces the secondary effect as a physical
          consequence (e.g., any landfill diversion unavoidably avoids methane)
        - The mechanism's function inherently provides multiple services
          (e.g., tree canopy inherently sequesters carbon AND reduces heat)

        A secondary is NOT inherent when:
        - It only applies in certain geographic contexts (handled in Stage 3)
        - It depends on implementation choices
        - It's a plausible but speculative co-benefit

        For each secondary assigned, explain the inherent causal chain.

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

    secondary_categories: str = dspy.OutputField(
        desc=(
            "Secondary categories INHERENT to this mechanism (apply to every "
            "instance regardless of location), comma-separated, or 'None'. "
            "Only assign if the mechanism unavoidably produces the secondary "
            "effect as a physical consequence."
        )
    )

    secondary_justification: str = dspy.OutputField(
        desc=(
            "For EACH secondary category, the inherent causal chain. Format: "
            "'<Category>: <explanation>'. Set to 'None' if no secondaries."
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
            "(2) Why this primary over alternatives, (3) Inherent causal "
            "chain for each secondary, (4) Edge cases applied, "
            "(5) Confirm labels apply to ALL instances."
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

class PolicyEnrichmentSignature(dspy.Signature):
    f"""Enrich an individual policy with instance-specific metadata.

    The primary and secondary categories have ALREADY been determined at the
    mechanism level (Stage 2). Do NOT override them. Your job is to add:

    1. Location-specific secondary categories (using vulnerability context)
    2. Instrument type and directness for THIS specific policy
    3. Climate relevance for THIS specific policy
    4. Key textual indicators from the source text
    5. Co-benefits specific to this instance

    You may ADD a secondary category based on location vulnerability context,
    but you may NOT remove or change the mechanism-level classifications.

    {CATEGORY_DEFINITIONS}

    ==================================================================================
    RULES FOR THIS STAGE:
    ==================================================================================

    Rule A — Location vulnerability context can ADD secondary types

        When location_vulnerability_context is provided, assess whether the
        policy mechanism intersects with a known local climate hazard in a way
        that justifies an additional Adaptation tag.

        The test: "Does this mechanism DIRECTLY reduce exposure or sensitivity
        to a climate hazard that this location faces?"

        Justified when:
        - Water reduction + location faces drought or water supply threats
        - Urban greening + location faces extreme heat
        - Building retrofit + location faces extreme temperature events
        - Coastal infrastructure + location faces sea-level rise

        NOT justified when:
        - The mechanism has no physical connection to the local hazard
        - The link is speculative or requires multiple intermediate steps

        When adding a secondary via vulnerability context, explain the
        causal connection in additional_secondary_evidence.

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
    assigned_secondaries: str = dspy.InputField(
        desc="Secondary categories from mechanism level — do NOT remove"
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
    additional_secondary: str = dspy.OutputField(
        desc=(
            "Additional secondary category justified by location vulnerability "
            "context, or 'None'. Only assign if the mechanism DIRECTLY reduces "
            "exposure/sensitivity to a local hazard."
        )
    )

    additional_secondary_evidence: str = dspy.OutputField(
        desc=(
            "Justification for additional secondary. Format: "
            "'<Category>: <location> faces <hazard>, and <mechanism> directly "
            "reduces exposure/sensitivity because <explanation>.' "
            "Set to 'None' if additional_secondary is 'None'."
        )
    )

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
    Stage 2: Classify each unique mechanism ONCE (with full category
             definitions, causal pathways, edge cases, and rules)
    Stage 3: Enrich each policy with instance-specific metadata
             (instrument type, directness, climate relevance,
             location-based secondary types, co-benefits)

    Consistency guarantee: Every policy sharing the same mechanism gets
    identical primary/secondary labels. Location-specific additions in
    Stage 3 cannot break mechanism-level consistency.
    """

    def __init__(self):
        super().__init__()
        self.extract_mechanism = dspy.ChainOfThought(MechanismExtractionSignature)
        self.classify_mechanism = dspy.ChainOfThought(MechanismClassificationSignature)
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
                "secondary_categories":      classification.secondary_categories,
                "secondary_justification":   classification.secondary_justification,
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
