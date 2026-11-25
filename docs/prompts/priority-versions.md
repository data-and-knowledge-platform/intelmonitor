# Priority prompt versions

Priority rates each development for each entity it is about as High, Medium or Low, with a one-sentence reason.
It is one Claude Sonnet 5.5 call per development and entity.
A development that names no entity is rated for its topics instead.

The live prompt is `PRIORITY_PROMPT` in `api/intelmonitor/pipeline/params.py`, and it is still version 1.
Every later version is a candidate in `CANDIDATES` in `api/intelmonitor/trial.py`, tried without changing the live prompt.
The version picked from these trials is switched on after the stored weeks are run again ([spec.md](../spec.md)), which has not happened yet.

This page covers versions 1, 5, 8, 10 and 13, the steps that each kept something.
Version 13 is the one carried forward.

## How versions are tried

A version number is given by the baseline store, in the order versions were first tried.
Each version is tried on the same 300 pairs of development and subject from the baseline store.
The pairs were drawn with a fixed seed, 100 at each of version 1's levels.
That is why version 1 shows 100 High, 100 Medium and 100 Low.

On 16 Nov 2026 Claude Opus 5.5 rated 126 of those pairs blind, as a judge.
81 of them were pairs where versions 1 to 5 disagreed, and 45 were controls.
The judge is one model pass, not human labels.
"Judge agreement" below is the estimated share of all 300 pairs where a version gives the judge's level.
The judge rated against the old Singapore anchor of versions 1 to 10, so it cannot score version 13 fairly.

## Summary

| Version | Candidate in trial.py | Effort | High | Medium | Low | Judge agreement |
|---|---|---|---|---|---|---|
| 1 | `live` | medium | 100 | 100 | 100 | 56.9% |
| 5 | `high-think` | high | 66 | 130 | 104 | 69.2% |
| 8 | `high-think-price-promo` | high | 59 | 119 | 122 | 71.9% |
| 10 | `high-think-price-promo-fi-lower` | high | 40 | 136 | 124 | 72.8% |
| 13 | `local-anchored` | high | 35 | 163 | 102 | not scored |

All versions run on Claude Sonnet 5.5.

## Version 1 (10 Nov 2026)

This is the team's High, Medium and Low prompt from their deck of 10 Nov 2026 (slide 7), as written.
It replaced an earlier rubric after the first round of user tests.
Each subject gets a Singapore phrase in `{singapore}`, chosen in `_singapore_phrase` in `api/intelmonitor/pipeline/subjects.py`.
For example, a foreign bank's phrase is its Singapore branch or subsidiary, and a country's phrase is Singapore's financial system.
High needs an effect on that phrase.

The problem: it rated too much High.
Of the 81 disputed pairs, the judge put 61 below version 1's level.

## Version 5 (16 Nov 2026)

The levels and rules are version 1's, unchanged.
The JSON lines at the end of the prompt are removed, since the structured output schema already carries each field's meaning.
Effort goes from medium to high, and the prompt ends with "Think the problem through before you answer." (`THINK_FIRST`).

Why: a sweep of effort and thinking settings (versions 2 to 5) showed this setting cut version 1's excess High the most.
Of the version 1 High pairs that version 5 moved to Medium and the judge rated, the judge put 26 at Medium and 9 at Low.

## Version 8 (16 Nov 2026)

This is version 5 plus rules 4 and 5.
Rule 4 says a finding that only reports a price move is never High: at most Medium for a Singapore market or a Singapore financial institution's shares, and Low otherwise.
Rule 5 says promotional and product news is Low.
A closing line keeps both rules to those findings only.

Why: user testing before 11 Nov 2026 said share price and market moves must never be High.
The rubric before 11 Nov enforced that, and the Low for promotional news, in code; version 1 dropped both.
The closing line came from version 6, whose wording without it moved 70 of the 300 pairs, most of them neither price moves nor promotional news.

## Version 10 (23 Nov 2026)

This is version 8 plus rules 6 and 7, which can only keep a finding from High and never raise one.
Rule 6 says a general effect on Singapore is not High on its own: market-wide statistics, deal or fee totals, rates or prices moving, or a foreign central bank's decision.
Such a finding can still be High if it names a financial institution in Singapore that it materially changes.
Rule 7 says a deal, investment or expansion by a company that is not a financial institution is at most Medium, unless it materially changes a financial institution in Singapore.

Why: the team reviewed the version 1 and version 8 workbooks on 18 Nov 2026 and flagged High rows such as these:
- Singapore's merger totals rated for Temasek.
- A US Federal Reserve rate rise rated for the United States.
- A data centre purchase and a share sale rated for Singtel.

Version 9 had a first wording of the same rules that also raised MAS announcements to High.
Version 10 adds that the rules never raise a level.

## Version 13 (25 Nov 2026)

This version anchors each level on the subject instead of on Singapore, following the team's subject guide of 23 Nov 2026 ("Subject = Topic + Entity").
The template replaces `{singapore}` with `{local_anchor}`, filled per subject by `_local_anchor_phrase` in `api/intelmonitor/pipeline/subjects.py`.
High means a material change for that anchor.

| Subject | Anchor | Example |
|---|---|---|
| Topic | MAS or Singapore's financial system | Cyber risks and incidents |
| Country | the country's financial system | United States' financial system |
| Region | the financial systems of the region | the financial systems of ASEAN |
| Sector | the sector | the housing sector |
| Bank, insurer, asset manager, payments or fintech firm, crypto firm, stablecoin issuer | the firm in Singapore | DBS in Singapore, Standard Chartered in Singapore |
| Tech provider, payment system or market infrastructure based in Singapore | the firm in Singapore | Singtel in Singapore, SGX in Singapore |
| Anything else: central banks, regulators, agencies, international bodies, foreign tech providers, investors | the entity itself | MAS, Federal Reserve, Microsoft Azure, GIC |

The rules change as follows:
- Rule 1 adds that, for a financial system, an effect on the economy, trade or people is not High unless the finding shows it reaching that system's financial institutions, markets or payments.
- Rule 2 asks for a link to the anchor instead of a Singapore link.
- Rule 4 caps price moves in the subject's own shares or in a market the anchor is directly exposed to.
- Rule 6 keeps market-wide statistics and deal or fee totals from High, and no longer lists a foreign central bank's decision.
- Rule 7 is dropped.

Why: the team's guide named two problems with version 10:
- Events in other countries, such as the US and China, were Low because they did little to Singapore's financial system. They should be rated for the country or entity they are about.
- News with a large effect on Singapore became High for the local banks through their strong links to Singapore; the guide's example is Indonesia cutting gas exports to Singapore. High should mean an effect on Singapore's financial system, not on Singapore in general.

The team also decided three things:
- A firm in and out of Singapore is rated for its Singapore branch only. The guide lists its global business as a later priority.
- A Singapore company's own large deals may be High for it, such as Singtel's data centre purchase.
- No separate rule on deals is wanted. Other subjects keep such deals at Medium or Low through rule 6 and their own anchors.

Compared with version 10:
- A US Federal Reserve rate rise is High for the United States.
- Singapore-wide effects are no longer High for banks.
- A Singapore bank's acquisition abroad is Medium unless the finding shows an effect in Singapore.

The known gap: MAS's own enforcement actions, notices and supervisory decisions rate Medium or Low, because nothing in the prompt says an authority's own actions are a material change for it.
The next section describes the planned fix.

The baseline's frozen seed records no entity kinds, so a trial takes each subject's anchor from the working tree's seed.
On 25 Nov 2026 all 119 frozen subjects were in the working tree's seed with unchanged kinds.

## Versions not covered here

- Versions 2 to 4 are the other effort and thinking settings of that sweep: medium effort, medium with thinking, and high without thinking.
- Version 6 was the first wording of rules 4 and 5, which moved too many pairs.
- Version 7 was rules 4 and 5 kept to their findings, at medium effort.
- Version 9 was the first wording of rules 6 and 7, which also raised MAS announcements to High.
- Version 11 was the first anchored version, which rated Singapore-based tech, payment and market infrastructure firms as whole groups.
- Version 12 rated those firms on their services to financial institutions in Singapore, which dropped their own incidents, such as a cyber attack and a regulator's fine, from High. It is retired.

## Deferred: the `role_rule` line

The plan is a second slot in the template, `{role_rule}`, filled with one sentence chosen by the subject's role.
Rule 1 would read "High is reserved for findings that affect {anchor}. {role_rule}".
The rest of the template stays the same for every subject, so a version is still one template and the trial and compare tools need no change.

It would fix the MAS gap.
In the version 11 sample, MAS lost High on five pairs and dropped from Medium to Low on one.
These included a licence revocation, prohibition orders, a cut to a bank's capital add-on and an amended notice.
The model read "a material change for MAS" literally, and found that MAS's own action does not change MAS.

The proposed sentences are these:
- **Authorities** (central banks, regulators, agencies, international bodies): "[Entity]'s own material decisions and actions, such as a rule, a policy rate decision, a licence revoked or an enforcement action, count as material changes for it."
- **Topics, countries and regions:** the financial system sentence that version 13 has in rule 1, moved into the slot.
- **Financial institutions:** "A serious event elsewhere in the group is High only if the finding shows it directly affects [the firm] in Singapore (e.g. its capital, liquidity, operations, staff, customers, or licence)." This sentence is in rule 1 today.
- **Sectors:** no sentence.

Making it needs these code changes:
- In `api/intelmonitor/pipeline/subjects.py`, a role per entity kind and a `role_rule` field on `PrioritySubject`.
- In `api/intelmonitor/pipeline/params.py`, `priority_system` fills `{role_rule}` as it fills `{local_anchor}`.
- In `api/intelmonitor/trial.py`, a new candidate.

The team still has to agree on these:
- Which kinds belong to which role. For example, is SGX an authority, since it regulates its listings, or a firm? Do payment systems such as PayNow and FAST count as firms? Do GIC and Temasek count as authorities, investors or financial institutions?
- The wording of each sentence.
- Whether a Singapore-based tech or payment firm needs a sentence of its own. Version 13 already rates it on itself in Singapore, so its own incidents stay High.

## Open question: REITs, GIC and Temasek

The team has not said whether REITs, GIC and Temasek count as financial institutions.
Under version 10, rule 7 treated them as non-financial, so their deals were at most Medium.
Version 13 has no rule 7, so the question now only decides the anchor:
- GIC and Temasek are investors today and are rated on themselves, so a large overseas deal of theirs can be High. For example, the GIC joint venture holding UK hospitals is High.
- As financial institutions, they would be rated as "GIC in Singapore", and an overseas deal would be Medium unless it shows an effect in Singapore.
- REITs are not entities today. They reach priority through the Property & REITs topic and the real estate and construction sector.

## Rerunning or comparing a version

Run these from `api/`:

```sh
# Rate the 300-pair sample with a candidate from CANDIDATES in trial.py.
uv run im trial priority --store ../data/intelmonitor-baseline-89-entities-2026-11-16.db --candidate local-anchored

# Write a workbook comparing two versions on the pairs both rated.
uv run im prompts compare priority 1 13 --store ../data/intelmonitor-baseline-89-entities-2026-11-16.db --out priority-v1-vs-v13-sample.xlsx
```

A call already recorded replays from the cache at no cost.
A new candidate costs about $1.20 for the 300 pairs.
Neither command changes the live prompt.
A trial records its calls in the baseline store, but writes no priority levels there.

A version whose prompt matches an earlier version's for most subjects replays those calls from the earlier version's records.
Version 13 has its own records for only 12 pairs; the other 288 are version 11's, which are identical.
`im prompts compare` only reads a version's own records, so it compares version 13 on those 12 pairs only.
The version 1 to version 13 workbook here was built by also reading version 11's records for the identical prompts, with no model call.

Both commands need the baseline store, `data/intelmonitor-baseline-89-entities-2026-11-16.db`, which is not in git (about 540 MB).
It holds six weekly runs, 4 Oct to 8 Nov 2026, for 89 entities, frozen on 16 Nov 2026, plus versions 1 to 13 and every recorded call.
Copy it by hand to a new machine.
Without it, the prompts can still be read in the code, but no version can be rerun or compared.
The judge's files are also outside git, in `artifacts/prompt-versions/judge-2026-11-16/`.

## Workbooks

These are in `docs/prompts/workbooks/`:
- `priority-v1-89-entities.xlsx`: version 1's level and reason for every development and subject it rated in the baseline, one sheet per weekly run, with a summary.
- `priority-v1-vs-v5-sample.xlsx`, `priority-v1-vs-v8-sample.xlsx`, `priority-v1-vs-v10-sample.xlsx`, `priority-v1-vs-v13-sample.xlsx`: each holds both versions' counts, both prompts, and for each run the pairs that moved, with headline, both levels and reasons, and sources.
