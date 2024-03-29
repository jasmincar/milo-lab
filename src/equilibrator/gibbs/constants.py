import numpy as np

R = 8.31e-3   # kJ/(K*mol)
F = 0.096485  # (kJ/mol)/mV
JOULES_PER_CAL = 4.184

# kJ/mol, formation energy of Mg2+
MG_FORMATION_ENERGY = -455.3

DEFAULT_TEMP = 298.15 # K
DEFAULT_IONIC_STRENGTH = 0.1 # mM
DEFAULT_PH = 7.0
DEFAULT_PMG = 14.0

PH_RANGE_VALUES = [5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0]

RT = R * DEFAULT_TEMP
RTlog10 = R * DEFAULT_TEMP * np.log(10)