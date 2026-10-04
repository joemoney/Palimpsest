# Pitch: The Gradual

**Status:** for the author to refine setting and theme. No template, no `story-writer` call yet.
**Universe:** the Consonance (`CONSONANCE_LORE_BIBLE.md`). A sibling to *The Missing Core* and *Rank: Null*, in a third place.
**Working title:** *The Gradual.* (A gradual is the book of chants sung between readings; "gradual" is also the
whole joke about a curriculum.) Alternates: *Teach the Dead*, *Continuing Education*.

## One-paragraph pitch

You are an adjunct instructor at a failing night college built on a wreck: you teach the safety course, the one
that says *do not touch that*. Under the college's cellar is the **Gradual**, a Consonance training hulk, dead for
four thousand years and the biggest thing in the district. It cannot build anything. No hull in this universe can.
What it can do is *recognise*: it answers living people, and only some of them, the same way the old hardware answers the
rare few in every belt. You find out it is a school, that the school is still grading, and that it
answers **a written method**. It has been waiting four thousand years for somebody to write the course. You are the
person who happens to write manuals. Everyone who completes a unit of your method becomes someone old
machinery will answer to, safely, on purpose. The district's whole economy starts to tilt around that. So does everyone
who would like to own it. And there is a question, which nobody on the page can answer, about what a civilisation
that taught a *standard method of being recognised* turned into.

## The hook, stated plainly

**You do not build the thing. You write the curriculum, and the thing is the graduates.**
The fantasy is the competence fantasy, scaled by teaching: not one hero levelling, but one author whose pupils each
level, and whose method compounds. The dread is the same dread the Consonance carries: a civilisation that standardised
how its minds met the machinery, then ended inside one generation.

## Theme

**Standardisation as both the gift and the fall.** A method that lets anyone be answered by old machines is the most
generous thing a teacher can leave. It is also the first step of the process that ended the Consonance (bible L-05).
The story's question is not *can you scale it* (you can) but *what do you leave out on purpose so that it does not
become unison.* The method has to be teachable, auditable, certifiable, and the more teachable it gets the more it looks
like the thing it must not become. The dissent has to be in the syllabus.

Secondary themes: **inheritance of a craft** (what a teacher owes a stranger who will teach it better), **who gets
certified** (the college, the Board, the Ossuary and the crews all want to own the credential), and **tuition as a moral
instrument** (what do you charge, and who does it keep out).

## Setting

*All names proposed; none appears in either source story.*

- **Stave Harbour.** A mid-sized scrap-and-trade settlement of a few thousand around a dim star, on a ring of
  Consonance wreck. Not a belt like the Garland (no crews at the edge of the wild) and not a Shelf (no mass muster). It is a
  town with a harbour board, a rent problem, and a trade college nobody respects.
- **The college.** *The Stave Harbour Technical Institute of Salvage Practice.* Underfunded, over-accredited, staffed
  by people who like it. Bureaucracy is a source of comedy and of real pressure: grade appeals, accreditation visits,
  a funding committee.
- **The Gradual.** A Consonance hulk, dormant, sealed in the college's old cellar and mistaken for a foundation. It
  is a *school-ship*: it trained the crews, attendants and operators who served cores. Its hardware is inert to almost
  everyone. It is not inert to a person who has completed enough of a taught sequence it recognises.
- **The Faculty.** The Gradual holds **nine dormant attendants**, each a *discipline*. Not one System but a department.
  A department wakes when its subject has enough graduates. They wake as people, slowly (bible L-08), and they have
  views on each other's curricula. Unlike TACET's lone Descant, the Faculty is plural, argumentative and
  institutional. Their presence is the story's warmth and its dread (do the nine disagree, or are they already unison?).
- **The four constants** (bible L-17) all arrive: the **Board** wants to license the credential and tax by graduate;
  the **Ossuary** sends an assessor to requisition the *method* (a person who can be told to write it down for them);
  the **Index** pays for the curriculum's history; the **crews** want the course for free and will teach it to each other badly.

## Protagonist

An adjunct instructor and technical writer. Competent, underpaid, mildly invisible, funny in the dry deadpan way of people
who write warnings for a living. **Not** a chosen one and not a prodigy: the thing they are good at is making a
thing teachable. Gender: open (the two sibling stories use a man; recommend a decision before the template, because
`world.rules` pins it). Default name TBD.

Why them (author-facing, `[X]`): the Gradual recognises not skill but *a person who writes down what they would warn a
stranger about.* It wants a teacher's method, not a pupil's talent, and nobody has been that kind of person near
it in four thousand years. (Optional layer: it was *selected* for, as in the first sibling story. Recommend not, so the
two stories do not rhyme in their central twist.)

## The mechanics, scrubbed of the source and mapped to the engine

The inspiration's genre vocabulary (letter-grade ranks, ability lists, colour-coded gear tiers, class trees, passive
income) is **replaced, not renamed.** No term, name or plot beat from it appears here. The new vocabulary is musical and liturgical, matching the Consonance's own naming
convention (Descant, Tacet, Threnody, Fermata, Precentor, Coda). All terms below are `[PROPOSED]`.

| Function | In *The Gradual* | Engine mapping |
|---|---|---|
| A person's trained speciality | **a Part** (a part in an ensemble: the Hand-part, the Pilot-part, the Reader-part…) | `spendable_ledger` unlock kind, per character; NPC records via `insert_character()` |
| What a Part grants | **a Mark** (a notation from the score, as in *Missing Core*'s "a mark from a score") | `spendable_ledger` entries (kind `skill`) |
| Advancing a Part | **an Audition** before the Faculty, a set-piece with a stake | A `gate` on a tier + a thread |
| Standing | **Seat** bands, five of them: *Tacit, Marked, Chaired, Principal, First* | `bounded_counter` stat with tier bands; `readout` token |
| Found hardware quality | **Pitch**: Flat, Natural, Sharp, Resonant | `tagged_items` tags |
| Practising a craft | **Practice**: hours spent, and hours *missing* (it costs, bible L-04/L-12) | Priced stats (`axes.<axis>.costs`); `per_turn` drift |
| Income from teaching | **Chair fees**: a share of what each graduate's work earns, which is the **economic engine and a moral problem** | `per_turn` drift on a funds axis; thread-gated |
| The support system | **the Faculty** (plural), not a single System | `scored_bonds` / `episodic_threads` per discipline |
| Scaling | **Cohorts**: graduate classes and what they go on to do | `episodic_threads` + a `bounded_counter` "enrolment" axis |
| Large-scale management (workshops, a yard) | **the Yard**: deliberately *not* an engine in v1; stays narration until authoring demands it | **The one real engine gap** (storyboard-first rule applies) |

Cost rule that makes it canonical (bible L-03, L-04): **every graduate pays a small, specific, unnoticed thing** to
be recognised: a habit of thought, an hour, a stubbornness. The method is generous and it is never free. Nobody builds anything.
Operating found hardware, safely, is all the course teaches.

## Tone

Deadpan competence comedy, in the register the two sibling stories already share, with an institutional flavour:

- Comedy is the work and the bureaucracy: rubric disputes, grade appeals, an accreditation visit that happens at the
  worst moment, a Faculty discipline that insists on citation format. Played straight; the humour is in the accuracy.
- The numbers go up and nobody explains what they count. (Bible L-10 reappears here: what does *Seat* measure?)
- The four comedy exceptions (bible E-8) apply unchanged: danger, death, anything the Auditor touches, intimacy.
- Dread arrives as absence. The first sign that the method is working *too* well is an exam room in which every
  candidate gives the same answer, in the same breath.

## Structure, and why this is the Endless Mode story

A term is a chapter. The natural cadence is **academic**: an intake, a teaching run, a set of exams, a climax
(**Convocation**: a public ceremony with a stake), then the next intake. That makes the Endless Mode cycle a
*calendar* instead of a contrivance. The bible's Chapter Shapes (Part E) map directly: The Job (field placement), The
Buyer, The Warrant (an Ossuary assessor), The Followed, The Four Kinds of Attention (accreditation), The Climb (a
graduate rising), The Hold (a sealed cohort).

Authored spine (three acts, as the sibling stories):

1. **The Safety Course.** The cellar, the first unit, the first graduate who is *answered*. Keep it quiet.
2. **Enrolment.** The method works; the district notices; the Board, the Ossuary and the Index each arrive with a
   different idea of what a credential is for. The Faculty starts to wake.
3. **Convocation.** The method is complete enough to be certified. What goes into the syllabus, and what stays out on
   purpose?

After those, the Endless Mode planner generates further terms from the bible. **No `commit_by`** (per the plan, the cycle
simply continues unless an authored destination becomes ready).

## Endings direction

Funnel destinations, with a catch-all (sketches, not authored):

- **Open Enrolment** *(destination)*: the method is released without a certifying body. It spreads, diverges, becomes
  uneven and local, and nobody can be sure it will not become unison. The dissent is in how many wrong versions exist.
  The one clearly *not-the-fall* ending, and not a triumph.
- **Convocation** *(destination, dark)*: the method is complete, certified, universal. Everyone answered. The exam
  room goes silent the way a channel does. From inside, the warmest thing that ever happened.
- **The Closed Book** *(destination)*: the method is burned, the hulk sealed again, the district keeps the rent.
  Four thousand years of waiting becomes four thousand and eleven.
- **Struck Off** *(catch-all)*: the college's accreditation lapses, the Board takes the cellar, the Ossuary takes the
  author. Nobody chose.
- **A terminal** (cf. D5): the author's own body (the cost of Practice) runs out.

Note the pairing with the two siblings: *Missing Core*'s endings are about **who sits in the seat**; *Rank: Null*'s
are about **whether to consent to a ranking**; *The Gradual*'s are about **whether to teach the method, and to whom.**
Three stories, one question about being joined, asked from three angles.

## What the pitch resolves, and what it leaves open

**Resolves the "Consonance tech cannot be built" conflict** by moving the protagonist's output from *construction* to
*operation.* The method teaches recognition and safe operation of found hardware; it never builds a part.

**Resolves the tone mismatch** by borrowing the competence-comedy register from *Missing Core*'s own dry physical comedy
and using institutional comedy where the source novel used power-fantasy comedy.

**Leaves open, for the author:**

1. **Open or fixed Faculty?** Nine disciplines is a guess. The Faculty is the story's cast and its *Descant*; fewer
   (three to five) means deeper characters and a more finite story.
2. **Is the Gradual lattice-made or Recusant-adjacent?** Recommended: made by the Consonance's own educators *before* the linking,
   and unresolved: its grammar may be the joining's grammar. (Bible Part F, 1.)
3. **How much of the economy is on the page?** Chair fees can be a light thread or a main subplot. Heavy economy
   pulls toward the one unbuilt engine (the Yard).
4. **Protagonist gender and name.** `world.rules` pins gender per the sibling pattern.
5. **Does the protagonist have a hidden reason to be recognised?** Recommended: no. A plain person who teaches well is the story.
6. **How much of Stave Harbour is a town (daily texture) versus a college (set pieces).**
7. **Where does it sit relative to the Garland and the Shelf?** (Bible Part F, 2.) Recommend: far enough that it
   shares nothing but the Ossuary, the Index and the Auditor.

## Suggested next step

You refine the setting, theme and open decisions above. When those are settled, the `story-writer` agent drafts
`stories/the_gradual/template.json` (final-path, `schema_version: 3`, with a `mechanics.endings` catch-all and an
`endless` block whose `lore` is cut from the bible per its "How to use" section), and lints to zero errors.
Engine work follows demand, per the build-order rule.
