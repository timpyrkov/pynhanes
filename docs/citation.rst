How the data were processed
===========================

This page describes every step between the files NHANES publishes and the table
``pynhanes`` writes, in the order they happen, so that a reader of your paper can
tell what was done to the numbers. It is meant to be **quoted or paraphrased in a
Materials and Methods section**; a ready paragraph is at the bottom.

Numbers below come from the codebook shipped with pynhanes 1.0.0, scraped on
2026-09-23: 12774 variables of 385 data files, surveys 1999-2000 to 2021-2023.


Data source
-----------

All data are the public releases of the National Health and Nutrition Examination
Survey (NHANES), downloaded from `wwwn.cdc.gov/nchs/nhanes
<https://wwwn.cdc.gov/nchs/nhanes/default.aspx>`_: the SAS transport (``.xpt``)
data files, the documentation pages that describe them, and the linked mortality
files (``*_MORT_2019_PUBLIC.dat``). Nothing is obtained from any other source and
nothing is imputed from outside the survey.


1. The codebook: one row per variable
-------------------------------------

The documentation page of every variable of every survey is read, and the result
is condensed into **one row per variable rather than one row per variable and
survey**, so that a single codebook decodes a table spanning 1999-2023. Pages are
read oldest survey first, and for a variable that appears again later:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Field
     - What the merged row contains
   * - Variable label
     - from the **newest** survey
   * - Value labels
     - the **union** of the value codes of all surveys; where the same code
       appears in several surveys, the label of the **oldest** wins
   * - Data files
     - every data file the variable appears in, most frequent first

Two consequences follow, and neither is hidden: a row can pair a new label with
old value meanings, and the value list can contain codes that no longer exist, or
did not yet exist, in the survey being analysed.

Of the variables that appear in more than one survey, about 39 % are identical
everywhere and a further 47 % differ only in the numeric range of a continuous
measurement. The remainder differ in wording (8 %), in the set of value codes
(7 %), or in whether the variable was written as a range or as categories (1 %).

A codebook of a single survey, with no merging at all, is written with
``pynhanes-scraper -s 2017``; comparing two of them is the quickest way to check
whether a variable changed between the surveys you use.


2. Choosing variables
---------------------

Variables are requested by human-readable name and resolved to NHANES variable
codes through the codebook. One name usually covers several codes, because NHANES
renames variables between surveys - family income is ``INDFMINC`` up to 2005 and
``INDFMIN2`` from 2007.

**A name that maps to several codes is refused rather than guessed**, because
some names cover different people: ``Gender`` is both the participant
(``RIAGENDR``) and the household reference person (``DMDHRGND``); ``Education
level`` is the adult scale, the children's grades and the reference person's.
The mapping actually used is a curated file, shipped with the package as
``pynhanes/data/nhanes_variables.json`` (341 names over 484 variable codes).


3. Reading the data files
-------------------------

Only the data files holding a requested variable are opened. Files of one data
file code are stacked across surveys, then the codes are joined side by side on
the participant identifier ``SEQN``, which NHANES keeps unique across surveys.

- **A participant appearing more than once in a file keeps only their first
  row**, with a warning naming the file. This concerns files with one row per
  medicine, per food, per tooth or per ear; only their per-person columns should
  be used.
- **A variable stored in several data files is merged rather than dropped** -
  hay fever (``AGQ030``) lives in ``MCQ``, ``RDQ`` and ``AGQ`` depending on the
  survey.
- **Mortality** comes from the newest public-use linked file present, so re-running
  after a new release changes who counts as deceased.

**The 2017-March 2020 pre-pandemic release is excluded by default.** NHANES could
not finish the 2019-2020 cycle and published the collected part combined with the
whole 2017-2018 cycle as ``P_DEMO``, ``P_BMX`` and so on, **renumbering the
participants**: ``P_DEMO`` holds 15560 people with ``SEQN`` 109263-124822 while
``DEMO_J`` holds 9254 with ``SEQN`` 93703-102956, and those 9254 appear in both
files under different numbers. Reading both would count them twice with no
collision to give it away, so either the two-year files or the pre-pandemic ones
are read, never both. CDC states that there is no way back to the 2017-2018
numbers, that the partial cycle represents no defined population on its own, and
that combining the release with earlier cycles requires re-weighted survey
weights scaled by the share of the period each cycle covers.


4. Codes that mean "no answer" become missing
---------------------------------------------

A value is emptied when its label in the codebook is one of *Refused, Don't know,
Missing, Blank, Error, Screened out, Unknown, No response, Could not determine /
obtain / interpret / assess, Cannot be assessed, Data acquisition problems, Text
present but uncodable, Blank but applicable*.

The same applies to labels saying the question or measurement did not apply -
*Not applicable, No result, No lab result, Not done, No examination, Not examined
(pregnancy, jewelry, weight over 300 lbs, other reason), Tested but VO2max
estimate missing*. These describe the interview rather than the person, so they
are treated as no data.

These are read from each variable's own codebook, because the numeric codes differ
between variables (7, 9, 77, 99, 777, 999, 7777, 99999 and others); **nothing is
inferred from the number itself.**

A few variables state the same thing in their own words, which no list can
recognise, and are named individually:

.. list-table::
   :header-rows: 1
   :widths: 25 12 38 25

   * - Variable
     - Value
     - Means
     - Becomes
   * - ``DED120``, ``DED125``
     - 3333
     - does not work or go to school
     - missing
   * - ``DBD090``, ``DBD091``, ``DBD895``
     - 6666
     - less than once a week
     - 0
   * - ``DBD090``, ``DBD091``, ``DBD895``
     - 5555
     - more than 21 times a week
     - 22

A value meaning "more than N" becomes the next number above the range, and "less
than N" the next below it, so the column remains numeric.


5. Truncated measurements are kept as published
-----------------------------------------------

NHANES replaces the end of some measurements by a single value: everybody older
than 80 is reported as 80, ``DED120`` reports 1 to 14 minutes as 14, a laboratory
result below the detection limit is given a fill value. These are kept as ordinary
numbers - they are the best the survey gives - so a mean is correct but the tail
of the distribution is not real. **293 of 12774 variables are affected** (159 at
the top, 106 at the bottom, 28 at both), and the codebook names each of them in
its ``Truncated`` column.


6. Yes/No becomes 1/0
---------------------

NHANES codes Yes as 1 and No as 2. No is rewritten to 0 so that a column can be
averaged, for variables whose only answers are Yes and No, for variables labelled
"Yes (checkbox checked)" / "No (checkbox unchecked)", and for gender, where Male
stays 1 and Female becomes 0.

**Variables with a third answer are not treated as binary.** Where the third
answer is not a No - diabetes "borderline" (``DIQ010``), physical activity "unable
to do it" (``PAD020``, ``PAD200``, ``PAD320``, ``PAQ650``, ``PAQ665``), air
quality "never thought about it" (``PAQ685``) - it is emptied rather than folded
into either side. Variables where 0 is itself a real answer are left untouched.

The codebook carries **both dictionaries**: the values as NHANES publishes them,
and the values after this step, so a parsed table can be read without repeating
the rules. Recoding is switched off with ``--no-recode``.


7. Merging the codes of one variable
------------------------------------

The codes of one variable become one column, **first code with a value wins**, so
the order in the variables file is the priority order.

**Yes/No variables are merged differently: a yes anywhere wins.** Their codes are
usually the same thing asked in different words - upper, mid and low back pain;
emphysema, chronic bronchitis and COPD - and the answer wanted is "yes, in at
least one of them".

**A measurement continuing under a new name stays a separate variable.** Where
NHANES replaced a data file rather than renaming a variable - blood pressure from
auscultatory ``BPX`` to oscillometric ``BPXO``, C-reactive protein from ``CRP``
(mg/dL) to ``HSCRP`` (mg/L), physical functioning from ``PFQ`` and ``DLQ`` to
``FNQ`` - those are different devices, units or questions, and they are given
names of their own rather than extending the old column.


8. Derived variables
--------------------

Some columns are computed rather than copied, and are switched off with
``--no-derived``:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Variable
     - What is computed
   * - Age
     - floor of the age in months (``RIDAGEEX``, then ``RIDEXAGM``, then
       ``RIDAGEMN``) divided by 12; the screening age in years where NHANES
       published no months, which is everybody over 19 from 2011 on
   * - Survey, Season of year
     - the survey and the season as text
   * - Poverty status
     - family income to poverty ratio cut at 2 and 4: 0 poor, 1 middle, 2 rich
   * - Income (family, household)
     - brackets of later surveys folded into those of the first ones
   * - Education level
     - the adult scale, filled in with the children's grade mapped onto it
   * - Language
     - English wherever it appears, then Spanish, then any other language
   * - Grip strength
     - the average of the three tests of that hand
   * - Screen time
     - hours of television and computer, added where the survey asks them apart
   * - Smoking status
     - 0 never, 1 quit, 2 current, from the cigarette, pipe and cigar questions
   * - Blood pressure
     - the average of the readings, **emptied** for participants who had food,
       alcohol, coffee or a cigarette within 30 minutes of the measurement
   * - Health care visits, hospital stays
     - later scales folded back to the earlier one before merging
   * - Mortality follow-up
     - months converted to years

Three columns are added **beside** the ones requested rather than instead of
them: any health insurance; the blood pressures **without** the 30-minute rule
above; and the sleep times as minutes after midnight next to the text.


9. Accelerometry
----------------

Minute-level movement exists for two eras: a hip monitor in 2003-2006
(``PAXRAW``, intensity counts and, from 2005, steps) and a wrist monitor in
2011-2014 (``PAXMIN``, MIMS triaxial units, ambient light and a
wake/sleep/non-wear label). Only the minute-level files are parsed; the hourly
and daily aggregates NHANES also publishes are not used as data.

Each array is one row per participant and one column per minute, and **column 0
is midnight of the first day of wear**. For 2003-2006 the first minute is already
midnight; for 2011-2014 the first day begins when the monitor was issued, so each
participant is shifted by that start time - verified against all 130186
person-days, every day boundary falls on an exact multiple of 1440 minutes.

Seven-day arrays are rolled so that column 0 is Monday 00:00, every array of one
participant by the same number of minutes. The nine-day array is left in calendar
order, because a roll wraps the end back to the front and would place the eighth
and ninth day before the first.

A minute carries a status: 0 missing, 1 wake wear, 2 sleep wear, 3 non-wear,
4 unknown. Only 2011-2014 has the wake/sleep/non-wear prediction; for 2003-2006 a
minute is either 4 (data present) or 0 (no data), which distinguishes an
unrecorded minute from a minute of no movement. A triaxial value of -0.01, which
NHANES defines as "value could not be computed", is stored as 0 and marked
missing in the status.


10. What the processing does not do
-----------------------------------

- **Survey weights are not combined.** ``WTINT2YR`` and ``WTMEC2YR`` are two-year
  weights; analysing several surveys together requires dividing them by the number
  of surveys used. They are copied unchanged, and **no analysis in the examples is
  weighted**.
- **Nothing is imputed.** A missing answer stays missing.
- **Values are not binned.**
- **Codes are not made comparable across surveys** beyond the cases listed above.
  Where NHANES changed what a code means, the codebook cannot show it.


Paragraphs you can use in a Materials and Methods section
---------------------------------------------------------

Three paragraphs, to be used together or separately: the questionnaire,
examination and laboratory data; the variables this package computes rather than
copies; and the accelerometry. Adjust the version, the scrape date and the last
sentence of each to what you actually did.

Data and processing
~~~~~~~~~~~~~~~~~~~

    Data were taken from the public releases of the National Health and
    Nutrition Examination Survey (NHANES, 1999-2023) and processed with pynhanes
    1.0.0 (https://github.com/timpyrkov/pynhanes), using the variable codebook
    scraped from the NHANES documentation pages on 2026-09-23. Because NHANES
    renames variables between survey cycles, variables were requested by name and
    resolved to NHANES variable codes through that codebook; a name mapping to
    several codes was resolved explicitly rather than automatically, so that
    participant-level variables were never mixed with those of the household
    reference person. Values whose codebook label denotes a non-answer
    ("Refused", "Don't know", "Screened out", "Not applicable" and equivalents)
    were set to missing, using each variable's own value codes rather than any
    fixed numeric convention. Binary Yes/No variables were recoded from 1/2 to
    1/0; variables whose third answer is not a "no" - diabetes "borderline",
    physical activity "unable to do it", air quality "never thought about it" -
    were not treated as binary and that answer was set to missing. Where a
    variable is published under several codes across cycles, the codes were
    merged into one column in a specified priority order, the first code with a
    value winning; Yes/No variables were instead merged as "yes in at least one
    code", since their codes ask the same thing in different words. A measurement
    continued on a different instrument or in different units - oscillometric
    rather than auscultatory blood pressure, high-sensitivity rather than
    standard C-reactive protein - was kept as a variable of its own rather than
    appended to the earlier column. Measurements that NHANES top- or
    bottom-codes were kept as published and are flagged as truncated. The
    2017-March 2020 pre-pandemic release was excluded, since it republishes the
    2017-2018 participants under new identifiers. Survey weights were not
    applied.

Derived variables
~~~~~~~~~~~~~~~~~

    A number of variables were computed rather than copied from the data files.
    **Age** is the age in completed years, taken as the floor of the age in
    months at examination (or at screening where the examination age is absent)
    divided by twelve, and the published age in years for participants for whom
    NHANES reports no age in months. **Smoking status** was coded as 0 never,
    1 former, 2 current, from having smoked at least 100 cigarettes in life
    together with the follow-up questions on smoking now and on ever having
    smoked regularly. **Education level** is the adult five-level scale, filled
    in for children by mapping the school grade they reported onto that scale.
    **Poverty status** was derived from the family income to poverty ratio cut at
    2 and 4 (0 poor, 1 middle, 2 rich), and **family and household income**
    brackets introduced in later cycles were folded back onto the brackets of the
    earliest ones. **Language** was coded as 1 English, 2 Spanish, 3 another
    language, English taking precedence wherever it is reported. **Blood
    pressure and pulse** are the average of the available readings, set to
    missing for participants who had eaten, drunk alcohol or coffee, or smoked
    within thirty minutes of the measurement; the same averages without that
    exclusion were retained as separate columns. **Grip strength** is the mean of
    the three tests of each hand, and **screen time** is the sum of the
    television and computer hours where a cycle asks for them separately.
    **Health care visits and hospital stays** were folded onto the scale of the
    earliest cycle before the codes were merged, and participants who answered
    that they had not been in hospital were assigned zero stays. **Mortality
    follow-up** was converted from months to years. Where a derived variable
    could not be computed because the variables it needs were absent, the
    published value was left unchanged.

Accelerometry
~~~~~~~~~~~~~

    Minute-level physical activity was taken from the two NHANES accelerometry
    releases: a hip-worn monitor in 2003-2006 (``PAXRAW``, activity counts per
    minute, and step counts from 2005-2006) and a wrist-worn monitor in 2011-2014
    (``PAXMIN``, MIMS triaxial units, ambient light, and a minute-level
    wake/sleep/non-wear classification). Only the minute-level files were used;
    the hourly and daily summaries NHANES also publishes were not. Each
    participant's record was placed on a fixed minute grid whose first column is
    midnight of the first day of wear: for 2003-2006 the published record already
    begins at midnight, and for 2011-2014 each record was shifted by the
    clock time at which the monitor was issued, which aligned every day boundary
    of all 130186 person-days onto an exact multiple of 1440 minutes. Seven-day
    arrays were rotated so that the first column is Monday 00:00, every array of
    a participant by the same number of minutes; the nine-day record was left in
    calendar order. Each minute carries a status - missing, wake wear, sleep
    wear, non-wear, or unknown - taken from the NHANES classification for
    2011-2014; for 2003-2006, which has no such classification, a minute is
    recorded only as holding data or not, so that an unrecorded minute is
    distinguishable from a minute of no movement. Triaxial values of -0.01,
    which NHANES defines as "value could not be computed", were set to zero and
    marked missing. Step counts were clipped to 0-255. A day was counted as
    usable when it held at least thirty minutes above threshold - non-zero for
    the single-axis measurements, thirty MIMS units for the triaxial one. The
    reliability and calibration flags NHANES publishes were carried through
    unchanged rather than used to exclude participants.

Where the numbers came from
~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you used the published tables rather than parsing the files yourself, the
first cell of the table's header records the version, the layout and whether the
values are recoded, for example::

    pynhanes 1.0.0 | names | recoded | derived | 2026-09-24

Read it back with ``pynhanes.parser.read_provenance(table)``.
