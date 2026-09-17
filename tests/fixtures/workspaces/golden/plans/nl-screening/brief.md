# Inception Brief: NL Screening

## Problem

Analysts express screening criteria in prose; the host's query model needs
structured criteria. Hand-translating loses fidelity and repeats work.

## Desired outcome

Natural-language phrases parse into the existing criteria AST, validate
against the screening vocabulary, and drive ranked results with
matched-criteria provenance.

## Initial thinking

Reuse the `query_search` AST and tokenizer; the new work is the grammar
table, the vocabulary validator, and provenance threading in the ranker.
Tier 3: the parser grammar is novel and the feature spans three packages.
