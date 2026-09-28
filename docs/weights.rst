Sample weights
==============

NHANES does not measure the whole country. It measures about 10,000 people per
cycle and lets each of them stand for a slice of the population. Almost every
published NHANES number depends on that, so this page explains what the weight
columns are, which one to use, and when you can ignore them.

Nothing here requires statistics beyond arithmetic. The worked examples at the
bottom can be pasted into a Python session as they are.


What a weight is
----------------

A sample weight answers one question:

    *How many people in the United States does this participant stand for?*

A typical weight is around 20,000 to 30,000. They are **inflation weights**, not
fractions, and within one cycle they add up to the whole civilian
non-institutionalised population:

===========  =============  ==================
Survey       Participants   Weights add up to
===========  =============  ==================
1999-2000    9,965          272 million
2003-2004    10,122         286 million
2009-2010    10,537         302 million
2015-2016    9,971          317 million
2021-2023    11,933         327 million
===========  =============  ==================

Because of that, you never rescale them, and weighted counts are real population
counts rather than numbers of participants.


Why there is more than one
--------------------------

People drop out at every stage of the survey, and each smaller group needs its
own weight so that the people who remain can stand for the whole country:

* everyone answered questions **at home**;
* fewer travelled to the **mobile examination centre** (body measures, blood
  pressure, most blood tests, the physical activity monitor);
* in 2021-2023 fewer still agreed to a **blood draw**;
* fewer came **in the morning without breakfast**, which is what fasting
  measurements need;
* a separate group completed a **dietary recall**.

NHANES publishes 95 different weights in total, most of them for small one-off
laboratory studies. pynhanes ships the five that its own variables need.


The columns pynhanes writes
---------------------------

``nhanes_userdata.csv`` carries seven columns for this:

================================  ==========================  ============================================
Column                            NHANES codes                Use it for
================================  ==========================  ============================================
``Sample weight``                 ``WTINT2YR``, ``WTINTPRP``  questions asked at home
``Sample weight (exam)``          ``WTMEC2YR``, ``WTMECPRP``  anything measured at the examination centre
``Sample weight (blood draw)``    ``WTPH2YR``                 blood tests in 2021-2023 only
``Sample weight (fasting)``       ``WTSAF2YR``, ``WTSAFPRP``  measurements that require fasting
``Sample weight (dietary)``       ``WTDRD1``, ``WTDRD1PP``    the day-one dietary recall
``Survey PSU``                    ``SDMVPSU``                 error bars (see below)
``Survey strata``                 ``SDMVSTRA``                error bars (see below)
================================  ==========================  ============================================

A weight of **zero** means the participant was not in that group at all: someone
who was interviewed but never examined has an exam weight of zero. Filter on
``> 0`` and they disappear, which is what you want.


Which weight for which variable
-------------------------------

``nhanes_weights_dict.csv`` answers that. Rows are pynhanes variable names, columns
are surveys, and each cell names the weight column to use:

.. code-block:: python

    import pandas as pd

    weights = pd.read_csv("CSV/nhanes_weights_dict.csv", sep=";", index_col=0)
    weights.loc["Hematocrit (%)", "2009-2010"]
    # 'Sample weight (exam)'
    weights.loc["LDL-cholesterol (mmol/L)", "2009-2010"]
    # 'Sample weight (fasting)'

The answer depends on the survey, because NHANES moves things around. General
health was asked at home in 2001-2004 and at the examination centre in every
other cycle, and the blood count tests moved onto their own blood-draw weight in
2021-2023.

A companion file, ``nhanes_weights_raw.csv``, gives the same information one
level down: rows are NHANES data files, columns are surveys, cells are the raw
NHANES weight variable. Use it if you parse codes that pynhanes does not curate.

**If a cell holds a raw NHANES code** such as ``WTTSTPP`` rather than one of the
column names above, that weight is not shipped. Add the code to your own
variables file and re-parse.

Regenerating the two tables
---------------------------

``nhanes_weights_dict.csv`` is written **every time you parse**, beside
``nhanes_userdata.csv``, because the two belong together: the parsed data carries
the weight columns, and the dictionary says which one each variable needs. Add
``--no-weights-dict`` if you do not want it.

``nhanes_weights_raw.csv`` ships with pynhanes and is written on request:

.. code-block:: bash

    pynhanes-scraper --weights              # the reviewed copy, no requests
    pynhanes-scraper --weights --refresh --store   # derive it again from the website

The second form rebuilds the table from scratch, which takes about five minutes
and roughly 1,600 requests. It needs no ``.xpt`` files.

.. note::

   **For an AI assistant, or anyone automating this.** NHANES does not publish
   the weight-per-data-file table. Each data file states its weight in the prose
   of its documentation page, and only about a quarter of those pages name one
   unambiguously, so ``nhanes_weights_raw.csv`` is *derived* and was reviewed by
   hand for the surveys in the shipped snapshot.

   For those surveys, **use the shipped file**. Running ``--weights`` without
   ``--refresh`` writes exactly that reviewed copy and costs nothing.

   For a **newly released survey** the file cannot be trusted blindly. Rerun with
   ``--weights --refresh --store`` and then check the new column by hand, paying
   attention to two things:

   * **questionnaires asked in the examination centre.** ``ALQ``, ``DPQ``, ``DUQ``
     and ``HSQ`` are answered at the exam, so they need the exam weight although
     their component says Questionnaire. The list is
     ``scraper.MEC_QUESTIONNAIRES`` and a new survey may add to it. To test a
     file, check whether every participant in it was examined
     (``RIDSTATR == 2``); if so, it is a centre questionnaire.
   * **a new weight for a whole component.** 2021-2023 introduced ``WTPH2YR`` for
     blood tests, which had never existed before. A cell naming a weight that
     pynhanes does not ship is a signal to read the NHANES release notes.

   If you would rather not review anything, copying the previous survey's column
   is right far more often than it is wrong -- but say so in your output rather
   than presenting the result as verified.

**Combining variables.** If your analysis mixes variables that want different
weights, use the one belonging to the **hardest-to-reach** group. Comparing age
(asked at home) with haematocrit (measured at the centre) means the exam weight,
because only examined people are in the analysis at all.


Important notes
---------------

**Pooling cycles.** Weights are built for one two-year cycle. If you analyse
several together, divide by the number of cycles, or you will count the country
once per cycle:

.. code-block:: python

    cycles = ["2013-2014", "2015-2016"]
    sub = table[table["Survey"].isin(cycles)].copy()
    sub["w"] = sub["Sample weight (exam)"] / len(cycles)

**Four-year weights are not shipped.** NHANES published ``WTINT4YR`` and
``WTMEC4YR`` for treating 1999-2002 as a single block. Both of those cycles also
have ordinary two-year weights, which is what pynhanes carries. The four-year
versions are *not* the two-year ones halved -- they were calculated separately --
so if you specifically want the 1999-2002 block, add the codes to your variables
file rather than dividing.

**When to use WTDR2D.** The dietary recall happens twice: once in person at the
examination centre, once by telephone a few days later. The two visits need
different weights.

* Using **day-one** variables only (codes beginning ``DR1``)? Use
  ``Sample weight (dietary)``, which is ``WTDRD1``. This is what pynhanes ships.
* Averaging **both days**, or using any ``DR2`` variable? You need ``WTDR2D``
  instead. Fewer people completed the second recall, so ``WTDRD1`` would
  overstate how many Americans they represent.

``WTDR2D`` is not in the shipped table. To get it, add it to your variables file
and re-parse:

.. code-block:: json

    "Sample weight (dietary 2-day)": ["WTDR2D", "WTDR2DPP"]

Never mix them: a two-day average weighted by the day-one weight is wrong, and
nothing in the data will warn you.

**Zero weights are stored as zero.** SAS writes the weight of somebody outside a
subsample as zero, but the XPORT format renders it as ``5.4e-79``. That is
harmless inside a weighted average and ruinous everywhere else -- ``weight > 0``
keeps the person, and ``log(weight)`` returns ``-78.3``. pynhanes forces anything
below ``1e-9`` to a true zero while parsing, so the shipped table is clean.

**Variables built from several files.** A few pynhanes columns merge codes that
live in files with different weights. Triglycerides is the clearest case: it
comes from the fasting panel in one file and the routine biochemistry panel in
another. The lookup names the weight of whichever file supplied most of the
values, and for triglycerides that is the routine panel, not the fasting one. If
the distinction matters to your analysis, parse the underlying codes separately
with ``--layout codes``.

**Accelerometry.** ``nhanes_activity.csv`` has no weight column, and the physical
activity monitor files are not rows of ``nhanes_weights_raw.csv``. The monitor was
handed out at the examination centre, so join the activity table to the parsed
table on ``SEQN`` and use ``Sample weight (exam)``. Bear in mind that dropping
participants with too few valid wear days -- which every accelerometry analysis
does -- is a second round of non-response that no NHANES weight corrects for.

**Mortality.** Death is an outcome, not a measurement, so it has no weight of its
own. The lookup gives the interview weight because that one is valid for everyone
in the linkage, but in a survival model the weight is decided by your
**covariates**: a model using blood counts needs the exam weight, whatever the
outcome is. The one exception is ``Mortality tte from exam (yr)``, which exists
only for people who were examined and so takes the exam weight itself - pair it
with examination covariates, and ``Mortality tte (yr)`` with interview ones. The
lookup leaves the mortality rows blank for 2017-2020 and 2021-2023, which are not
linked to the death index.


What PSU and strata are for
---------------------------

The survey cannot scatter 10,000 people across 10,000 towns -- the examination
centre is a convoy of trailers that has to drive to them. So the country is cut
into about 15 **strata** (groups of counties, similar in region, city-versus-rural
and demographics), two counties are picked in each, and several hundred people
are measured in every county. Those counties are the **PSUs**, short for primary
sampling units.

In the data:

* ``Survey strata`` takes a value like 75 to 89. The numbering continues across
  cycles and never repeats, so you can stack surveys without renumbering.
* ``Survey PSU`` is **1, 2 or occasionally 3**. It is not a county identifier. It
  only means "the first county visited in this stratum" and "the second one", and
  it has no meaning outside its own stratum.

That last point matters: PSU 1 in stratum 75 and PSU 1 in stratum 80 are
different places. Survey software calls this *nesting*, and forgetting to switch
it on is the most common NHANES mistake.

Here is the division for 2009-2010, all 10,537 people::

              PSU 1  PSU 2  PSU 3
    stratum
    75          379    424      0
    76          419    366      0
    77          441    382      0
    ...
    86          303    360    283
    ...
    89          107    144      0

The split in one line:

    **Weights decide who the sample represents. PSU and strata decide how sure
    you are allowed to be.**

They are separate jobs, and no amount of weighting replaces the design.


Tutorial 1: weights remove bias
-------------------------------

NHANES deliberately samples some groups more heavily than others, so a plain
average of the participants is not an average of the country. The weights undo
that. Here is a synthetic country where the true answer is known:

.. code-block:: python

    import numpy as np
    rng = np.random.default_rng(0)

    # 100,000 people; 20% belong to a group with a higher value
    minority = rng.random(100_000) < 0.20
    value = 120 + 10 * minority + rng.normal(0, 10, 100_000)
    truth = value.mean()

    # sample 500 from each group -- the minority is oversampled 4x
    take = lambda who, n: rng.choice(np.where(who)[0], n, replace=False)
    picked = np.concatenate([take(minority, 500), take(~minority, 500)])

    # weight = how many people each participant stands for
    weight = np.where(minority[picked],
                      minority.sum() / 500,
                      (~minority).sum() / 500)

    y = value[picked]
    print(f"truth            {truth:.2f}")
    print(f"plain average    {y.mean():.2f}")
    print(f"weighted average {(weight * y).sum() / weight.sum():.2f}")

Running it prints something close to::

    truth            122.04
    plain average    125.43
    weighted average 122.61

The plain average lands about three points too high, because too many members of
the higher group are in the sample. The weighted average lands back near the
truth. The whole formula is::

    weighted average = sum(weight * value) / sum(weight)


Tutorial 2: PSU and strata fix the error bar
--------------------------------------------

This one you can check with a calculator. A toy survey: four strata, two counties
each, three people each.

.. code-block:: python

    import numpy as np, pandas as pd

    df = pd.DataFrame(
        [(1, 1, 1000, 44), (1, 1, 1000, 45), (1, 1, 1000, 43),
         (1, 2, 1200, 39), (1, 2, 1200, 40), (1, 2, 1200, 38),
         (2, 1,  900, 47), (2, 1,  900, 46), (2, 1,  900, 48),
         (2, 2, 1100, 41), (2, 2, 1100, 42), (2, 2, 1100, 40),
         (3, 1, 1500, 43), (3, 1, 1500, 44), (3, 1, 1500, 42),
         (3, 2,  800, 45), (3, 2,  800, 46), (3, 2,  800, 44),
         (4, 1, 1300, 38), (4, 1, 1300, 39), (4, 1, 1300, 37),
         (4, 2, 1000, 46), (4, 2, 1000, 47), (4, 2, 1000, 45)],
        columns=["stratum", "psu", "w", "y"])

    # step 1: the average
    mu = (df.w * df.y).sum() / df.w.sum()

    # step 2: each person's pull on that average
    df["z"] = df.w * (df.y - mu) / df.w.sum()

    # step 3: add the pulls up inside each county
    county = df.groupby(["stratum", "psu"]).z.sum()

    # step 4: inside each stratum, how much do its two counties disagree?
    #         with two counties this is just the squared difference
    var = sum(np.diff(g.values)[0] ** 2 for _, g in county.groupby(level=0))

    print(f"average {mu:.2f} +/- {1.96 * np.sqrt(var):.2f}   (design)")
    print(f"average {mu:.2f} +/- {1.96 * df.y.std(ddof=1) / np.sqrt(len(df)):.2f}   (naive)")

Look at the data first: people inside one county are alike (43, 44, 45) while the
two counties of a stratum differ (44-ish against 39-ish). That gap is the entire
point.

The design error bar comes out about twice the naive one. Stratum 3, whose
counties agreed, contributes almost nothing; stratum 4, whose counties disagreed
sharply, dominates the total. Which gives the idea in one sentence:

    **Your uncertainty comes from how much the counties disagreed with each
    other, not from how many people you measured.**

Measuring ten thousand more people inside those same eight counties would barely
move the error bar -- and the naive formula never notices.


Tutorial 3: ignoring the design invents discoveries
---------------------------------------------------

This is the reason the two columns matter. Two laboratory values with **no**
relationship whatsoever inside a person, each drifting a little from county to
county:

.. code-block:: python

    import numpy as np
    from scipy import stats
    rng = np.random.default_rng(7)

    def one_run():
        xs, ys, cs = [], [], []
        for county in range(30):
            cx, cy = rng.normal(0, 1.5), rng.normal(0, 1.5)   # local drift
            xs.append(cx + rng.normal(0, 1, 30))              # 30 people
            ys.append(cy + rng.normal(0, 1, 30))
            cs += [county] * 30
        return np.concatenate(xs), np.concatenate(ys), np.array(cs)

    naive, aware = [], []
    for _ in range(400):
        x, y, c = one_run()
        naive.append(stats.pearsonr(x, y)[1])
        mx = [x[c == k].mean() for k in np.unique(c)]         # one point per county
        my = [y[c == k].mean() for k in np.unique(c)]
        aware.append(stats.pearsonr(mx, my)[1])

    print(f"naive test calls it significant in {np.mean(np.array(naive) < 0.05):.0%} of runs")
    print(f"county-aware test                  {np.mean(np.array(aware) < 0.05):.0%} of runs")

The naive test declares a significant correlation about **60%** of the time. The
correct figure is 5%; the county-aware test gives 4%.

Nothing about this involves weights. It happens in exactly the kind of
person-level analysis where people assume the design can be ignored.


When you can skip the weights
-----------------------------

Weights and design do different jobs, and only one of them is optional:

* **Describing the country** -- "what fraction of American adults have anaemia",
  "average blood pressure by age group". Weights are **required**. Without them
  you are describing the participants, not the country.
* **A relationship inside a person** -- "does haematocrit fall with age".
  Weights are usually **optional**, provided your model already contains the
  variables NHANES oversampled on: age, sex, race and ethnicity, income.
  Weighting here costs precision and buys little.
* **Error bars, p-values and confidence intervals** -- ``Survey PSU`` and
  ``Survey strata`` are **always** needed, weighted or not. Tutorial 3 shows what
  happens otherwise.

One more trap. To study a subgroup -- adults aged 20 to 60, say -- do **not**
delete the other rows before computing error bars. Dropping rows can empty a
county and break the variance calculation. Survey packages take a subgroup
argument that keeps the full design in place: ``subset()`` in R's *survey*,
``subpop`` in Stata.


Handing it to survey software
-----------------------------

pynhanes does not estimate anything itself. It gives you the three columns that
survey packages ask for:

.. code-block:: r

    # R, the survey package
    design <- svydesign(ids = ~`Survey PSU`, strata = ~`Survey strata`,
                        weights = ~`Sample weight (exam)`, nest = TRUE, data = df)
    svymean(~`Hematocrit (%)`, design, na.rm = TRUE)

.. code-block:: python

    # Python, the samplics package
    from samplics.estimation import TaylorEstimator

    est = TaylorEstimator("mean")
    est.estimate(y=df["Hematocrit (%)"],
                 samp_weight=df["Sample weight (exam)"],
                 stratum=df["Survey strata"],
                 psu=df["Survey PSU"])

``nest = TRUE`` is the switch that says PSU numbers restart inside every stratum.
Leave it out and the software treats every "PSU 1" in the country as one giant
county.
