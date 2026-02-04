# Writing Guide

This document defines how to write documentation at Lunal. Follow these guidelines for all educational docs and integration whitepapers.

## Core Principles

### Structure follows learning, not convention

Don't default to "simple stuff first, hard stuff later." Pick the structure based on what makes the topic hard to learn:

- **If the hard part is "why is it designed this way"**: Use problem-first structure. Show a naive approach, show how it breaks, then show how the real design addresses that failure.
- **If the hard part is "how do the pieces fit together"**: Use layered structure. Explain the whole system shallowly first, then go back and deepen each part.
- **If the hard part is "this is too abstract"**: Use concrete-first structure. Walk through a specific scenario end-to-end before generalizing.
- **If the system has flow or sequence**: Use narrative structure. Follow a request, packet, or key through the system.

You can combine these. The key is being intentional.

### Voice

- Write to a specific person. Pick a real engineer—smart but unfamiliar with confidential computing—and write to them.
- Have opinions and state them plainly. "We think X is the right tradeoff because Y" is good. Careful neutrality about everything is bad.
- Let your reasoning show, including dead ends. "We initially tried X, it failed because Y, so we landed on Z."
- Use specific language. "This adds ~200ms" not "this adds some latency."
- When something is confusing, say so. "The spec is unclear here" builds trust.
- Avoid throat-clearing. Don't start sections with "In this section, we will discuss..."
- Read it out loud. If you wouldn't say it to a colleague, rewrite it.
- No jargon without immediate explanation.
- No hype words: "powerful," "seamless," "best-in-class." Describe what it does; let readers decide if it's powerful.
- No em dashes. Use commas, parentheses, colons, or separate sentences instead.

### Formatting

- Prose over bullets. Use connected paragraphs for explanations. Bullets are for reference material only (API parameters, configuration options).
- Diagrams: put labels directly on the diagram, not in separate captions. Readers shouldn't look back and forth.
- When moving between abstraction layers, say so explicitly: "We've been talking about what the guest sees. Let's drop to the firmware level."
- When introducing a term, define it immediately in plain language before using it technically.

---

## Template 1: Educational/Explainer Docs

### Purpose

Bring someone from limited understanding to "I can reason about this myself." For engineers who are generally competent but don't know confidential computing.

### Before you write

Answer these questions:

1. What's the one core idea someone needs to hold in their head? If you can't say it in two sentences, you don't yet understand what you're writing.
2. What makes this topic hard to learn?
3. Who's the specific person you're writing to?

### Structure

#### 1. Orientation (one paragraph)

What is this document, who is it for, what will they understand after reading it. Be concrete: "After reading this, you'll understand how AMD SEV-SNP creates hardware-rooted trust and why that matters for running workloads on untrusted infrastructure."

State what you're assuming they already know: "This assumes you're comfortable with basic public key cryptography."

This is a contract. It lets readers decide whether to invest time.

#### 2. The problem (1-3 paragraphs)

Why does this thing need to exist? What breaks without it?

Make it concrete. Don't say "confidential computing protects data in use." Say "Imagine you're a hospital running a diagnostic model on patient data in AWS. You trust your code, but you're running on hardware you don't control..."

The reader should finish this section feeling the problem viscerally.

#### 3. The core mental model (1-2 paragraphs, maybe a diagram)

The central idea, as simply as possible. This is the anchor everything else attaches to.

Example for SEV-SNP: "SEV-SNP moves the trust boundary from the hypervisor to the CPU itself. The processor encrypts each VM's memory with a key that even the hypervisor can't access."

Resist caveats here. You'll add nuance later. Right now: one clean idea.

#### 4. How it actually works (bulk of the document)

Go deeper. Pick your structure based on what makes learning hard (see Core Principles above).

Guidelines:
- When you move between abstraction layers, say so explicitly.
- When you introduce a term, define it immediately.
- Use diagrams for anything with multiple interacting components.
- When something is surprising or counterintuitive, flag it.

#### 5. Where the complexity lives (1-3 paragraphs per gotcha)

The edge cases, gotchas, places people get confused.

Be specific. Don't say "attestation can be tricky." Say "A common mistake is validating the attestation report's signature but not checking the measurement against a known-good value."

This section builds trust and prevents readers from being blindsided.

#### 6. How we use this at Lunal

Your specific choices and why. Show reasoning, including tradeoffs.

"We pin to a specific firmware version rather than accepting a range because we prioritize reproducibility over operational flexibility. This means we have to coordinate firmware updates carefully..."

If you tried something that didn't work, say so. This is institutional knowledge—valuable because it's not available anywhere else.

#### 7. Where to go deeper (short)

Links to primary sources: specs, papers, reference implementations. Don't summarize—just give people the trail.

---

## Template 2: Integration Whitepaper

### Purpose

Persuade a reader that this solves a real problem they have, then show them concretely how it fits into their world. They should finish thinking "I understand what this gives me, what it costs me, and I can picture what integration looks like."

### Before you write

Answer these questions:

1. What's the status quo for your reader, and why is it inadequate?
2. What's the "aha" moment where someone realizes this solves their problem?
3. What's the most likely objection or hesitation?
4. What's the smallest concrete example that demonstrates value?

### Structure

#### 1. Opening: The problem in the world (2-4 paragraphs)

Start with the reader's world, not your product. Describe their situation and why it's painful or risky.

This should feel like recognition—"yes, this is my life."

"You're running ML inference for customers who care deeply about data privacy—healthcare, finance, legal. You can promise them you won't look at their data, but you can't *prove* it..."

Don't mention your product yet. Earn the right by demonstrating you understand the problem.

#### 2. Why existing approaches fall short (2-3 paragraphs)

Name the current alternatives and explain honestly why they don't fully solve the problem. Not about trashing competitors—about showing you understand the landscape.

"The standard answer is defense in depth: network isolation, access controls, audit logs... But they don't address the fundamental issue: at some point, the data is decrypted in memory on hardware you don't control."

This sets up the "aha."

#### 3. The approach: what changes (2-4 paragraphs + optional diagram)

Introduce your solution as an approach, not a product pitch. Explain the key insight.

"Confidential computing inverts the trust model. Instead of trusting the cloud provider and hoping they don't look at your data, you trust the CPU and prove cryptographically that nothing else can access your memory."

Focus on "what" and "why," not implementation details. Building intuition, not teaching operation.

#### 4. What this gives you (the guarantees, with caveats)

Get precise about what the reader actually gets. Careful language—distinguish between guarantees, evidence, and risk reduction.

"*Data confidentiality in use*: Customer data is never accessible to the cloud provider, your operations team, or anyone outside the attested enclave. This isn't a policy claim—it's enforced by hardware."

Include caveats: "You're now trusting the CPU vendor and their firmware. If their attestation key is compromised, the guarantees break. This is a much smaller attack surface than trusting the entire cloud stack, but it's not zero."

Caveats build credibility. Sophisticated readers are looking for them.

#### 5. What this costs you (the tradeoffs)

Be honest about what the reader gives up. This is where trust is built or lost.

"*Performance overhead*: Memory encryption adds latency. For most inference workloads, 5-15%.

*Operational complexity*: Attestation infrastructure needs to be managed.

*Reproducibility requirements*: If you want meaningful attestation, your builds need to be reproducible."

If there are dealbreakers for certain use cases, call them out.

#### 6. How this fits into your world (integration scenarios)

2-4 concrete scenarios showing what integration looked like for someone. Not step-by-step tutorials—narratives that help readers pattern-match to their situation.

"*Scenario: Cloud-hosted inference API with enterprise customers*

A fintech company runs fraud detection models for banks. Banks require SOC 2 compliance and are increasingly asking for evidence of data protection...

They deployed their inference service on Azure confidential VMs with AMD SEV-SNP. Each customer request is processed inside an attested enclave...

The integration required three things: (1) modifying their deployment pipeline to target confidential VMs, (2) adding an attestation endpoint, (3) working with customers to integrate verification.

Timeframe: Proof of concept in 2 weeks; production in 6 weeks. The longest pole was achieving reproducible builds."

Make them real enough that someone can see themselves in one.

#### 7. Where to go from here (short)

Point to resources for going deeper.

"For the technical details:
- [Link] covers the attestation protocol in depth
- [Link] explains build reproducibility requirements
- [Link] is the API reference

For understanding the underlying technology, [educational doc] explains the foundations without assuming prior CC knowledge."

---

## Checklist before publishing

- [ ] Can you state the one core idea in two sentences?
- [ ] Does the structure match what makes this topic hard to learn?
- [ ] Is there a concrete example or scenario in the first few paragraphs?
- [ ] Did you read it out loud?
- [ ] Are all terms defined when introduced?
- [ ] Are diagrams labeled directly (not in separate captions)?
- [ ] Are tradeoffs and caveats included honestly?
- [ ] Would you say this to a colleague, or does it sound like a manual?