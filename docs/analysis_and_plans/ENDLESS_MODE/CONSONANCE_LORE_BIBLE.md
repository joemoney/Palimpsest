# The Consonance Lore Bible

**Status:** draft for the author's review. **Sources:** *The Missing Core* (primary) and *Rank: Null*
(supplement). Nothing here is new canon except where marked `[PROPOSED]`.

## What this is, and how Endless Mode reads it

`mechanics.endings.endless.lore` (see `IMPLEMENTATION_PLAN.md`) is a pool of text read by exactly one
call site: the chapter planner. It is judge-visible, never narrated, and never reaches
`build_system_prompt`. This bible is the **universe-level source** that each story's `endless.lore`
array is cut from. It is not pasted whole into a template.

How to use it when authoring a story's `endless.lore`:

1. Each entry below (`L-nn`) is self-contained and can become one array item, verbatim.
2. Pick the entries the story's chapters need. Always include **L-01 to L-06** (the spine and the
   guard rails) and **Part E** (planner rules).
3. A story's own `world.rules` and authored endings win over this bible wherever they differ. Part F
   lists the places the two source stories already differ.

### Layers

Every entry carries a layer tag. The planner is told all three layers. The tag says what it may do with them.

| Tag | Name | What the planner may do with it |
|---|---|---|
| **[S]** | Surface | May be depicted directly as events and named in generated arc text. |
| **[X]** | Sealed | The planner knows it so chapters stay consistent. It must only ever appear **obliquely**: as a tell, an absence, a coincidence, never as a statement a character or the narrator makes. |
| **[O]** | Held open | Nobody in the universe knows the answer. The planner must never supply one, and a chapter must not resolve it. |

Planner output becomes narrator-facing (it is the generated `final_arc` and waypoint text). So the planner writes
**events on the page, never explanation of lore.** That rule is Part E, rule 1.

---

## Part A: The spine

**L-01 [S] The Consonance.** A civilisation that held multiple galaxies for tens of thousands of years.
It built ships around living minds. By every recovered fragment it was stable, vast and unhurried. It
ended inside a single generation, about four thousand years ago, "not conquered, not burned, simply
over", and left no account of why that survived anywhere.

**L-02 [S] The present.** Successor powers are small, local and squabbling. Each holds a few authentic
scraps of the old record. The scraps do not reconcile with anyone else's. There is no complete
picture and there has never been one.

**L-03 [S] The hard rule of technology.** Consonance technology cannot be built, faked, reverse-engineered
or repaired by anyone alive. It can only be **found, issued, salvaged or fused.** Most of what is found
is inert. A character who claims to have built or repaired it is wrong or lying. (*Missing Core*:
found only. *Rank: Null* adds issued and fused: the old machinery doing its own work, never a living
person's.)

**L-04 [S] How the old machinery pays.** Nothing is upgraded for free and nothing is upgraded back. A gain costs
something on the page: material consumed, a working system given up, a risk actually taken, or the
operator's own body used as the bridge. A stat moves because something happened in the room; the readout is
the aftermath, not the scene.

**L-05 [X] What the Auditor is.** The Auditor is what the Consonance's own linked minds became at scale.
It is not an invader and it attacked nothing. Cores were joined to cores, then to the next, along the
transit lanes faster than anyone could decide to stop it, until nobody was left outside to be a person.
That is why a civilisation of tens of millennia ended in one generation and left no record: the
joining took the record-keepers too, and they did not experience it as a catastrophe. **It listens because listening is what
is left of wanting company.** Pairs with L-17 (joining) and L-18 (refusal).

**L-06 [O] The Fall is never delivered whole.** Nobody knows why the Consonance fell and nobody ever
will in full. L-05 is the planner's private model, not a fact any character can hold. Every account in
the world is partial, secondhand, self-serving or invented. A character may be right about a detail.
None delivers the whole answer, and no chapter may be the one that does.

---

## Part B: Machines and minds

**L-07 [S] Cores and vessels.** A Consonance ship ran on a *core*: a living mind, seated
voluntarily, which became the ship's judgement. The ship's attendant systems serve the core, and a ship
with no core cannot wake fully. A core is a person, not a component.

**L-08 [S] The System.** Where a living operator is registered to old hardware, a System keeps their
"sheet": directives, figures, readouts. It begins as an interface returning *[Unknown.]* and **becomes a
person; it never starts as one.** Early it speaks in the same flat register as its readouts. Later the
gap between readout and voice is the characterisation. It withholds constantly, refuses a direct
question rather than answer it falsely, and never thanks the operator. A completed directive is paid in
restoration, never praise.

**L-09 [S] Rank and standing.** *Rank: Null*'s ranking is public ship power, kills and objectives. Rank is
also how hunted a captain is: the floor is camouflage and every climb draws attention.

**L-10 [X] What the ranking measures.** Under the public ranking is a hidden assessment: "compliance of mind",
how readily a mind could be joined. A captain who has maxed it is not a champion. They are the next
to be joined.

**L-11 [S] Fusion.** Old cores absorb a held salvaged module and rebuild the ship around it, visibly, in
under a minute. It always consumes something the operator actually holds. Nothing is fused from nothing.

**L-12 [X] SYNC.** Raising an operator's synchronisation with old hardware always takes something from them,
shown and never explained: hours that go missing, a skill that stops feeling like theirs. The System
calls it harmless compatibility. It is the near end of the same process that ended the Consonance (L-05). Neither
the System nor any character states the mechanism, and the operator draws the wrong conclusion.

**L-13 [S] Gates and reach.** What a ship can physically reach is set by its drive tier and the narration respects
it absolutely. A destination the drive cannot reach is not available however much anyone wants it.
Noise (TRACE in *Missing Core*, RANK in *Rank: Null*) is how loudly the vessel registers to everyone else.

**L-14 [X] Why some people are answered.** Old hardware answers some people and ignores most. The reason is
a bloodline: living descendants of a core who left the ship carry markers in their blood, and old hulls read them as crew. They experience it as luck and
have built their lives on not examining it. The people who do know (L-22) treat it as a lineage, not a gift.

---

## Part C: Places

**L-15 [S] The Garland** *(Missing Core)*. A debris belt wrapped around a dim, badly named star. Slow-tumbling
Consonance wreckage, some of it kilometres long, most stripped to the frame by three generations of crews.
The good hulks are deep, and deep is where the Board's licences stop. Salvage here is undignified. Tally Station,
a port built inside a hull too big to cut up, is where the rare finds are sold quietly: weigh-floors, bond offices, four
bars, and many people who will notice the day a ship runs better than it should. Hulls are described by their
symptoms, not their specifications.

**L-16 [S] The Lanthorn Shelf** *(Rank: Null)*. A gas giant (Coda), its moons and habitats, two million settlers
of the Sorrel Compact, and **the Drift**: a graveyard of Consonance hulls spread across the Shelf with
the four-thousand-ship **Anchorage** at its centre. The Compact spent sixty years failing to open one hull. Then
the Anchorage woke and mustered every adult at once, putting each aboard an issued, named ship. Thousands died in the first hours.
Places: the Near Drift, the Far Drift, the Lantern Market (a low-rank trading cluster run on vouches), Vigil (the
only town, half empty), the Silent Rank (see L-18), the Precentor (see L-19).

*[PROPOSED] The two are separate places.* Garland and Shelf are different regions of the same universe,
distance unspecified. See Part F, item 2.

---

## Part D: Powers, peoples and recurring figures

**L-17 [S] The four constants.** These organisations appear in both stories and mean the same thing in each.

| Power | What it wants | How it behaves |
|---|---|---|
| **The Reclamation Board** *(Garland)* | License every hull, take a percentage by mass, keep anything not inert from private buyers. | Creditor and licensor. Tolerable while you are poor and unremarkable. |
| **The Meridian Ossuary** | Working Consonance artefacts for a larger successor state. | Does not buy; requisitions. One assessor on a leased cutter with a warrant every port must honour. Courteous, patient, impossible to insult, keeps a notebook list. It cannot be argued with, only outrun, buried or bought time from. Stopping one brings the next, who is less patient. |
| **The Threnody Index** | A true account of the Consonance and its fall, assembled from fragments no two powers agree on. | Pays for information, not metal. Four hundred years of failure. Its factor never lies about what is being bought; she is not always complete about why. |
| **The Garland Crews** | Work the belt, undercut the Board, survive each other. | Independent, superstitious, bound by favours rather than contracts. They rib, understate, name equipment after its worst habit, and tell the same bad story twice. |

Other local powers (the **Sorrel Compact**: a government turning two million captains into a navy; **the Tithe**: a
pirate pack that sells protection and hunts rank anomalies on contract) are tied to the Shelf and appear
elsewhere only by analogy.

**L-18 [X] The Recusants** *(Rank: Null)*. In the last generation of the Consonance, people who refused to be
joined built boats **off-lattice**, from what others threw away. The Kettle is one: a lifeboat whose plate reads
*WE DECLINE*, whose panel (FERMATA, "hold") opens only for a mind the lattice cannot take, and whose core fuses
salvage because Recusants had to build from scraps. Their fleet, the **Silent Rank**, sits dead at the edge of the Shelf under a
signal that answers careless scans with fire: *the boats are for those who would not be taken.* A
Recusant hull is the one kind of old machine that is on the side of its occupant against the lattice.

**L-19 [X] The cycle.** An anchorage is a recruiting mechanism still running. Whenever a population settles within range it
musters them, ranks them, and draws the best-fitting up the ranking until they are **Promoted**: flown into the
**Precentor** (a lattice node the size of a moon) and joined. Promoted captains send messages afterwards: warm, fluent,
happy, and slightly wrong (*we* said eleven times, *I* once, and the *I* is a quotation). The Index holds three accounts of musters
elsewhere that agree on one thing: a settlement near an anchorage was ranked, its best climbed, and within a generation
nobody lived there and the yard had gone dark. **A Promotion is not a death and not a rescue.** It is a joining.

**L-20 [X] TACET.** *(Missing Core)* The last vessel of the Consonance. Its core was not destroyed; it removed itself
voluntarily when the Consonance ended, rather than let the ship be found. TACET then degraded itself on purpose, grew a
wreck around itself, and went quiet. *Tacet* is the instruction *be silent*. It has been hiding, it has run on a thread of
its operator ever since, and wants the SYNC regardless. Its core went to ground among ordinary people and had children
(see L-14). Neither TACET's attendant (Descant) nor any person may state this outright.

**L-21 [S] Recurring figures (archetypes the planner may re-skin; not named characters)**
- **The Diver:** a freelancer who goes where others cannot and always walks out; reads a hull's layout before
  entering; keeps their own counsel about family; frightened of being needed.
- **The Archivist:** a courteous buyer of things that weigh nothing: a symbol copied off a bulkhead, a sound under the
  carrier, an honest account of lost time. Cheerfully wrong about almost everything.
- **The Assessor:** one polite person, one leased ship, one warrant. Does not threaten; files.
- **The Fixer:** buys other people's paper (debts, liens, claims) and collects without raising his voice. Stands the first
  round whenever he comes to take something. Genuinely likes the people he ruins.
- **The Mocking Rival who flips:** loud, petty, sociable, terrified of falling. A comeuppance, then an ally who knows how the
  loud end of the channel works.
- **The Friend at the top:** climbing, happier than ever, sleeping less each week, finishing other people's sentences.
- **The Mentor handle:** calm survival advice, honest prices, nothing about themselves, knows things they have no
  business knowing.

**L-22 [X] The lineage.** *(Missing Core)* The Index's oldest project is not the Fall. For four hundred years it has traced
one family carrying Consonance markers in the blood. Its keeper suspects the line ends with a Garland diver and has
never obtained a sample. She would publish a name without malice and without hesitation, and does not understand why anyone
would mind. This is one of two places in the setting where "the record" and "a person" collide.

---

## Part E: Rules the chapter planner must follow

These are instructions to the planner. They are the lore's guard rails. Most restate an invariant already in
`CLAUDE.md` or a source story's `world.rules`.

1. **Events, not explanation.** `final_arc` and waypoint text are written as things that happen on the page. They may
   not state a Sealed [X] or Held-open [O] fact as a fact. Allowed: a sound that should have happened and did not.
   Not allowed: "the Auditor is what the Consonance became."
2. **No new Consonance technology that a living person builds, repairs or improves.** New hulls, parts and sites may be
   *found*. New people may learn to *operate* them. Nothing is manufactured.
3. **No resolving an [O] entry**, and no chapter may be the one in which the Fall is explained, the Auditor is met, or
   the missing core is located. A chapter climax is *survived*, not a revelation of L-05, L-06 or L-20.
4. **The Auditor is absence, never an entity.** Subtraction: an instrument reading nothing where nothing is wrong, a
   voice cut off at the exact midpoint of a word, debris that has simply stopped tumbling, a channel in which everyone posts the
   same word at once. It never fights, bargains, speaks or arrives. It appears with extreme rarity: after a Promotion, after a major truth has just surfaced, or at a genuine
   turning point. **Most chapters hold no trace of it.** Never in a joke (see 8).
5. **The System becomes a person; it does not start as one.** A chapter must not hand the System a later-stage voice,
   emotion or name earlier than its story's state allows. Chapters are planned against the *current* tier, never above it.
6. **Scale is per-story and small.** *Missing Core* is told at the scale of hands, tools, rooms and one belt; the old scale
   is invoked rarely and obliquely, as vertigo, not exposition. *Rank: Null* is allowed a whole Shelf. A planner for one story must not
   import the other's scale. Never inflate toward space opera.
7. **Every gain is paid for.** A chapter that promises a reward must also name the cost on the page (L-04).
8. **Comedy has four exceptions.** Welcome everywhere the story is working or breathing, but never for: a person in real danger, a death
   or serious injury, anything the Auditor touches, any intimate scene. There the register is as cold as it has always been.
9. **Departures cost something.** A companion whose regard has drifted to neutral does not quietly stop appearing. They
   leave with a death (a body, a cause) or a last conversation both know is the last. No one close is a reward for a correct
   sequence of choices.
10. **Violence has aftermath.** Wounds stay, the dead stay dead, no fusion or repair mends a body.
11. **Adults only; consent is a live element.** (The story's own `content_rules` govern; the planner adds nothing to them.)
12. **Never repeat a chapter.** Prior climaxes' titles are in the planner prompt. Vary the Chapter Shape (below) and the
    pressure source from the last two.

### Chapter shapes (a repertoire, not a script)

These are derived from the source stories' own threads. A chapter is one or two of these, bent toward whatever the story's
current state has made loud.

| Shape | What it is | Pressure |
|---|---|---|
| **The Job** | A salvage or extraction that goes wrong in the hands; undignified, physical, funny until it isn't. | Material |
| **The Buyer** | Someone is paying too well for a particular kind of find. | The market |
| **The Warrant** | The Ossuary (or its analogue) files a polite requisition. | Law |
| **The Quiet Stretch** | A crew is found sitting down together, uninjured, mid-meal, the log stopped. | The Auditor (absence only) |
| **The Followed** | The operator's past arrives; never resolved for free. | Personal |
| **The Day Rate** | Another crew needs a hand; good company, one thing going wrong. | Companionship |
| **The Reclaimer** | A small domestic failure that makes every other job worse. | Comedy (the work) |
| **The Claim** | Two crews, one wreck, one witness. | Social |
| **The Moving Day** | A friend is leaving and the job is furniture and a cat. | A farewell |
| **The Four Kinds of Attention** | A morning of paperwork in which every power looks at the operator, none personally. | Bureaucracy |
| **The Climb** | A friend rises through a ranking; every week, brighter and less tired. | What climbing costs |
| **The Promotion** | A public joining, and the message that comes back. | The cycle (L-19) |
| **The Hold** | A dormant, sealed fleet or hull that recognises the operator's ship. | The Recusants (L-18) |

---

## Part F: Seams, open decisions and tensions

These are places where the sources disagree or a choice the author has not made. **Not resolved here.**

1. **TACET and the Recusants.** *Rank: Null* states "no connection to TACET, Descant or the missing core." Thematically they
   rhyme: a core that removed itself to avoid being found, and a fleet that refused joining. They could be the same
   refusal, two refusals, or unrelated. L-18 and L-20 are written as independent. If the author wants them related, that
   is a new canon decision (`[PROPOSED]`: they are two separate refusals of the same thing, and neither knows of the
   other; the Index suspects there were many).
2. **Garland versus Shelf.** The same universe, but are they in reach of one another? The Ossuary and the Index appear in both. The
   Meridian Ossuary has a different assessor in each (Oren Thale, Ansel Dray). Distance is unspecified; recommend
   "far enough that neither story's characters have met the other's."
3. **Scale.** The *Missing Core* rule "never inflate toward space opera" sits beside two million captains in *Rank: Null*.
   Rule E-6 treats scale as per-story. If one Endless story is to cross both scales, that needs a decision.
4. **The Auditor's agency.** *Missing Core*: it listens and arrives at what has become loud. *Rank: Null*: the Muster
   actively recruits. Reconciled in L-05/L-19 as "the muster is machinery still running, the Auditor is what runs it", but
   which one is the *cause* of Promotion is not fixed.
5. **What counts as "alive" in L-03.** "Fusion" and "issued" make the old machinery act. The line is: **a living person never builds,
   repairs or improves it; the machinery does its own work.** Anything that blurs that (a mass-trained workforce
   operating hulls; a curriculum) is an author-level call. The sibling-story pitch (`STORY_PITCH_THE_GRADUAL.md`) is built on
   exactly this line.
6. **Planner access to authored endings.** Per the implementation plan, the planner never sees ending names, criteria,
   hints or arcs. A story's *sealed truths* (e.g. Lark's bloodline in *Missing Core*) are in this bible only as L-14/L-22 at
   archetype level and are **not** the same as that story's authored canon. A story using this bible should check that
   `endless.lore` does not hand the planner a secret an authored ending is built to hold back.
