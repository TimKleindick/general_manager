# Forecast scenario comparison

Reference 1.17 accepts two equivalent representations of an asserted forecast
scenario for a year: the ordered pair `[low, high]` and the closed object
`{"low": low, "high": high}`. The forecast fact comparator applies this
compatibility only to `forecast.scenario` year entries. Other dictionaries and
lists retain their existing comparison rules.

The object must contain exactly `low` and `high`. Both values still use the
existing finite numeric comparison, so equivalent numeric strings and numbers
are accepted. Wrong or reversed bounds, missing or extra years or bounds,
nonfinite values, booleans, and contradictory additional claims fail. No bounds
are inferred from percentages, source data, or expected values. An unavailable
forecast with no asserted scenario retains `null`.

All other forecast facts, units, populations, provenance, fact support, and
semantic checks remain mandatory. Comparison preserves the original extracted
facts in the result and does not rewrite the observation.

References 1.10 through 1.16 retain strict list comparison. Historical saved
extractions and scores are unchanged; any assessment with reference 1.17 must
be recorded separately with its selected reference and source version.
