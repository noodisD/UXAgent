"""The sentences the harness writes when it forces the agent out of a stuck loop.

experiment.py emits them; attribution.py and iso_metrics.py recognise them. They
used to be written out in three places, and narrowing the markers in one of them
stopped recognising the other sentence: there are two, worded differently
("Ta metoda nie działała…" for the loop-breaker, "Ten element nie działał…" for
the circuit breaker), and the test fed only one.
"""

FORCED_BACK_METHOD = "Ta metoda nie działała mimo kilku prób — wracam, by spróbować innej drogi."
FORCED_BACK_ELEMENT = "Ten element nie działał mimo kilku prób — wracam i wybieram inną drogę."

# Lower case, as descriptions are compared. Whole phrases, not fragments of them:
# a persona's own "wybieram inną wersję wyposażenia" is not a tool failure.
FORCED_BACK_MARKERS = ("nie działała mimo kilku prób", "nie działał mimo kilku prób")
