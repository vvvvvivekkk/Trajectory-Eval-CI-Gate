# Docs: learn this project from zero

These docs exist because the repo itself is written for someone who already
knows what an eval is. This folder assumes you don't.

It is written for a person who wants to become an AI engineer and is learning
the fundamentals properly — including the case where you have built things
with AI help and now want to actually understand the code underneath.

## Reading order

| # | Doc | What you get | Time |
|---|---|---|---|
| 00 | [Start here](00-start-here.md) | What this project is, in one page, and why this particular skill is worth having | 10 min |
| 01 | [Concepts from zero](01-concepts.md) | Agents, tools, trajectories, evals, baselines, CI gates. No assumed knowledge | 30 min |
| 02 | [Walkthrough: run it yourself](02-walkthrough.md) | Every command, and what every line of output means | 45 min |
| 03 | [The flow, end to end](03-the-flow.md) | One task traced from prompt to exit code, with diagrams | 30 min |
| 04 | [Every file explained](04-file-by-file.md) | What each file does and why it exists | 40 min |
| 05 | [The code, read line by line](05-code-explained.md) | The important functions, explained in plain English | 90 min |
| 06 | [The Python you need](06-python-you-need.md) | Every Python feature this repo uses, taught from the repo's own code | 90 min |
| 07 | [The maths, worked by hand](07-the-maths.md) | Precision, recall, F1, LCS, weighted averages, medians — on real numbers from this repo | 45 min |
| 08 | [Extend it: exercises](08-extend-it.md) | Nine exercises, easiest first, with answers | 4-8 hrs |
| 09 | [How to talk about it](09-talking-about-it.md) | Explaining this work in an interview without overclaiming | 30 min |
| 10 | [Glossary](10-glossary.md) | Every term in one place | reference |

If you only have an hour: read 00, then do 02 with the terminal open.

## How to use these docs

Read with the repo open in one window and a terminal in the other. Every code
reference names a file and a function, so you can go and look at the real
thing. Where a doc quotes numbers, those numbers came from running the code —
you can reproduce all of them.

Two warnings worth taking seriously:

- **Do the exercises in [08](08-extend-it.md).** Reading about an eval and
  building one are different skills, and only the second one is hireable.
- **Nothing here is a tutorial you should follow blindly.** The design
  decisions in this repo are defensible, not correct-by-definition. Doc 09
  lists the places a working engineer might reasonably disagree, because
  being able to argue about a trade-off is the actual signal.
