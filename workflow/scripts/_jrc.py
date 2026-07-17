"""Helpers for standardising JRC processing."""

TERTIARY_CARRIERS = {
    "Advanced electric heating": "electricity",
    "Biomass": "biofuel",
    "Biomass and waste": "biofuel",
    "Biomass and wastes": "biofuel",
    "Conventional electric heating": "electricity",
    "Conventional gas heaters": "gas",
    "Derived heat": "heat",
    "Diesel oil": "oil",
    "Distributed heat": "heat",
    "Electric space cooling": "electricity",
    "Electricity": "electricity",
    "Electricity in circulation and other use": "electricity",
    "Gas heat pumps": "gas",
    "Gas/Diesel oil incl. biofuels (GDO)": "oil",
    "Gases incl. biogas": "gas",
    "Geothermal energy": "renewable_heat",
    "Geothermal": "renewable_heat",
    "Liquified petroleum gas (LPG)": "oil",
    "Natural gas": "gas",
    "Solar": "renewable_heat",
    "Solids": "solid_fossil",
}
TERTIARY_END_USES = {
    "Space heating": "space_heat",
    "Space cooling": "end_use_electricity",
    "Hot water": "hot_water",
    "Catering": "cooking",
}
TERTIARY_ENERGY_SHEET= {
    "final_energy": "SER_hh_fec",
    "useful_energy": "SER_hh_tes",
}
