"""Twenty-one original practice questions for the demo bank.

Written from scratch for this repository: simple arithmetic and everyday if-then logic.
They are grouped into seven archetypes, each named for the mistake pattern its items test.

Every item carries a machine check, and make_sample_bank.py marks an item verified only when
its check passes:
  * arithmetic items carry the computed answer, which must equal the keyed choice;
  * logic items carry a tiny propositional model (variables, premises, one predicate per
    choice). The check enumerates every true/false assignment that satisfies the premises
    and requires the keyed choice to be the ONLY choice true in all of them.
"""
from fractions import Fraction as F
from itertools import product
from math import comb, perm

MATH_PATTERNS = {
    "Successive percent changes": "Adds or cancels percent changes instead of multiplying the factors.",
    "Ratio part vs. whole": "Treats one part of a ratio as if it were the whole.",
    "Combined work rates": "Averages the times instead of adding the rates.",
    "Order matters or not": "Counts ordered arrangements when order does not matter, or the reverse.",
    "Remainder cycles": "Grinds out a large power instead of using the repeating remainder cycle.",
}
LOGIC_PATTERNS = {
    "If-then direction": "Reverses an if-then statement (the converse) instead of using its contrapositive.",
    "Only if": "Reads 'P only if Q' as 'if Q then P'.",
}


def m(archetype, difficulty, stem, choices, key, explanation, value):
    return dict(section="Math", archetype=archetype, pattern=MATH_PATTERNS[archetype],
                difficulty=difficulty, stem=stem, choices=choices, key=key,
                explanation=explanation, target_seconds=90, check=("value", value))


def lg(archetype, difficulty, stem, choices, key, explanation, names, premises, preds):
    return dict(section="Logic", archetype=archetype, pattern=LOGIC_PATTERNS[archetype],
                difficulty=difficulty, stem=stem, choices=choices, key=key,
                explanation=explanation, target_seconds=75, check=("logic", names, premises, preds))


imp = lambda p, q: (not p) or q   # noqa: E731  material conditional

QUESTIONS = [
    # ---- Math: successive percent changes
    m("Successive percent changes", "Easy",
      "A price of $80 is raised by 25 percent, and the new price is then cut by 20 percent. What is the final price, in dollars?",
      ["76", "78", "80", "82", "84"], "C", "80 × 1.25 = 100, then 100 × 0.80 = 80.", F(80) * F(5, 4) * F(4, 5)),
    m("Successive percent changes", "Medium",
      "A town's population grows by 10 percent in one year and by 10 percent again the next year. By what percent did it grow over the two years?",
      ["11", "19", "20", "21", "22"], "D", "1.1 × 1.1 = 1.21, a 21 percent increase, not 20.", (F(11, 10) ** 2 - 1) * 100),
    m("Successive percent changes", "Medium",
      "A $200 investment loses 50 percent of its value and then gains 50 percent. What is it worth now, in dollars?",
      ["100", "150", "175", "200", "250"], "B", "200 × 0.5 = 100, then 100 × 1.5 = 150.", F(200) * F(1, 2) * F(3, 2)),
    # ---- Math: ratio part vs. whole
    m("Ratio part vs. whole", "Easy",
      "A class of 30 students has boys and girls in the ratio 2 : 3. How many girls are in the class?",
      ["10", "12", "15", "18", "20"], "D", "2 + 3 = 5 parts of 30 / 5 = 6 students each. Girls = 3 × 6 = 18.", F(30 * 3, 5)),
    m("Ratio part vs. whole", "Medium",
      "A bag holds red and blue marbles in the ratio 3 : 5. There are 25 blue marbles. How many marbles are in the bag?",
      ["15", "25", "40", "45", "75"], "C", "Blue is 5 parts, so one part = 25 / 5 = 5 marbles. The bag holds 3 + 5 = 8 parts = 40.",
      F(25, 5) * 8),
    m("Ratio part vs. whole", "Medium",
      "A mix uses flour and sugar in the ratio 4 : 1 by weight. How many kilograms of sugar are in 2 kilograms of the mix?",
      ["0.4", "0.5", "1.6", "2", "8"], "A", "4 + 1 = 5 parts, so sugar is 1/5 of 2 kg = 0.4 kg.", F(2, 5)),
    # ---- Math: combined work rates
    m("Combined work rates", "Easy",
      "Pipe A fills a tank in 6 hours and pipe B fills it in 3 hours. Working together, how many hours do they take?",
      ["1", "1.5", "2", "3", "4.5"], "C", "Rates add: 1/6 + 1/3 = 1/2 tank per hour, so 2 hours.", 1 / (F(1, 6) + F(1, 3))),
    m("Combined work rates", "Medium",
      "One painter finishes a fence in 4 hours and another in 12 hours. Working together, how many hours do they take?",
      ["3", "4", "6", "8", "16"], "A", "1/4 + 1/12 = 4/12 = 1/3 fence per hour, so 3 hours. Averaging the times (8) is the trap.",
      1 / (F(1, 4) + F(1, 12))),
    m("Combined work rates", "Medium",
      "Printer A prints a job in 20 minutes and printer B prints the same job in 30 minutes. Together, how many minutes do they take?",
      ["10", "12", "15", "25", "50"], "B", "1/20 + 1/30 = 5/60 = 1/12 job per minute, so 12 minutes.", 1 / (F(1, 20) + F(1, 30))),
    # ---- Math: order matters or not
    m("Order matters or not", "Easy",
      "How many different 3-person committees can be chosen from 6 people?",
      ["15", "18", "20", "30", "120"], "C", "Order does not matter: 6C3 = (6 × 5 × 4) / (3 × 2 × 1) = 20.", comb(6, 3)),
    m("Order matters or not", "Medium",
      "A club of 5 people chooses a president and a vice president. How many different choices are possible?",
      ["10", "20", "25", "60", "120"], "B", "The two roles are different, so order matters: 5 × 4 = 20.", perm(5, 2)),
    m("Order matters or not", "Medium",
      "Seven people meet and every pair shakes hands once. How many handshakes take place?",
      ["7", "14", "21", "42", "49"], "C", "A handshake is an unordered pair: 7C2 = 21. 7 × 6 = 42 counts each one twice.", comb(7, 2)),
    # ---- Math: remainder cycles
    m("Remainder cycles", "Medium",
      "What is the remainder when 2^10 is divided by 7?",
      ["1", "2", "3", "4", "5"], "B", "2^3 = 8 leaves remainder 1, so 2^9 leaves 1 and 2^10 leaves 2. Check: 1024 = 7 × 146 + 2.", 2 ** 10 % 7),
    m("Remainder cycles", "Medium",
      "What is the units digit of 3^20?",
      ["0", "1", "3", "7", "9"], "B", "Units digits of 3^n cycle 3, 9, 7, 1 every 4 steps. 20 is a multiple of 4, so the digit is 1.", 3 ** 20 % 10),
    m("Remainder cycles", "Hard",
      "What is the remainder when 3^7 is divided by 5?",
      ["0", "1", "2", "3", "4"], "C", "3^n mod 5 cycles 3, 4, 2, 1. 7 = 4 + 3, so the remainder matches 3^3 = 27, which leaves 2.", 3 ** 7 % 5),
    # ---- Logic: if-then direction
    lg("If-then direction", "Easy",
       "Every square is a rectangle, and every rectangle has four sides. Shape S is a rectangle. Which statement must be true?",
       ["S is a square.", "S is not a square.", "S has four sides.", "S has four equal sides."], "C",
       "Being a rectangle guarantees four sides. It does not settle whether S is a square: that would be the converse.",
       ("square", "rect", "four", "equal"),
       lambda v: imp(v["square"], v["rect"]) and imp(v["rect"], v["four"]) and imp(v["square"], v["equal"]) and v["rect"],
       [lambda v: v["square"], lambda v: not v["square"], lambda v: v["four"], lambda v: v["equal"]]),
    lg("If-then direction", "Easy",
       "If it rains, the match is cancelled. The match was not cancelled. Which statement must be true?",
       ["It rained.", "It did not rain.", "The match was played indoors.", "It rained and the match was played indoors."], "B",
       "Contrapositive: not cancelled means it did not rain. Nothing is said about indoors.",
       ("rain", "cancel", "indoors"),
       lambda v: imp(v["rain"], v["cancel"]) and not v["cancel"],
       [lambda v: v["rain"], lambda v: not v["rain"], lambda v: v["indoors"], lambda v: v["rain"] and v["indoors"]]),
    lg("If-then direction", "Medium",
       "Every member of the chess club also plays piano. Dana plays piano. Which statement must be true?",
       ["Dana is in the chess club.", "Dana is not in the chess club.", "Dana plays piano or is in the chess club.",
        "Dana is in the chess club and plays piano."], "C",
       "Only the given fact (Dana plays piano) is certain, so the 'or' statement holds. Membership is the converse trap.",
       ("chess", "piano"),
       lambda v: imp(v["chess"], v["piano"]) and v["piano"],
       [lambda v: v["chess"], lambda v: not v["chess"], lambda v: v["piano"] or v["chess"], lambda v: v["chess"] and v["piano"]]),
    # ---- Logic: only if
    lg("Only if", "Easy",
       "You may enter only if you have a ticket. Ali was allowed to enter. Which statement must be true?",
       ["Ali has a ticket.", "Ali is a guest of honor.", "Ali has no ticket.", "Ali has a ticket and is a guest of honor."], "A",
       "'Enter only if ticket' means entering requires a ticket, so Ali has one.",
       ("enter", "ticket", "guest"),
       lambda v: imp(v["enter"], v["ticket"]) and v["enter"],
       [lambda v: v["ticket"], lambda v: v["guest"], lambda v: not v["ticket"], lambda v: v["ticket"] and v["guest"]]),
    lg("Only if", "Medium",
       "The alarm sounds only if the door is open. The door is open. Which statement must be true?",
       ["The alarm is sounding.", "The alarm is not sounding.", "The door is open or the alarm is sounding.",
        "The door is closed."], "C",
       "An open door is required for the alarm, not a trigger for it, so the alarm could be either way.",
       ("alarm", "door"),
       lambda v: imp(v["alarm"], v["door"]) and v["door"],
       [lambda v: v["alarm"], lambda v: not v["alarm"], lambda v: v["door"] or v["alarm"], lambda v: not v["door"]]),
    lg("Only if", "Medium",
       "A plant flowers only if it gets sunlight. Plant P got no sunlight. Which statement must be true?",
       ["P flowered.", "P did not flower.", "P was watered.", "P was not watered."], "B",
       "Flowering requires sunlight; without sunlight P cannot have flowered.",
       ("flower", "sun", "water"),
       lambda v: imp(v["flower"], v["sun"]) and not v["sun"],
       [lambda v: v["flower"], lambda v: not v["flower"], lambda v: v["water"], lambda v: not v["water"]]),
]


def check(q):
    """True when the item's machine check confirms its key."""
    kind = q["check"][0]
    idx = "ABCDEFGH".index(q["key"])
    if kind == "value":
        value = q["check"][1]
        if value is None:
            return False
        try:
            return F(q["choices"][idx]) == F(value).limit_denominator(1000)
        except ValueError:
            return False
    _, names, premises, preds = q["check"]
    if len(preds) != len(q["choices"]):
        return False
    models = [dict(zip(names, bits)) for bits in product((False, True), repeat=len(names))]
    models = [v for v in models if premises(v)]
    if not models:
        return False
    always = [k for k, p in enumerate(preds) if all(p(v) for v in models)]
    return always == [idx]
