"""Tests for the punctuation-aware chunker - the dual-streaming seam."""

from __future__ import annotations

import pytest

from f1_commentator.orchestrator.chunker import chunk_by_punctuation
from tests.fakes import async_iter


async def _collect(deltas, **kwargs) -> list[str]:
    return [c async for c in chunk_by_punctuation(async_iter(deltas), **kwargs)]


@pytest.mark.asyncio
async def test_flushes_at_hard_boundary():
    chunks = await _collect(["Verstappen ", "wins", "!"])
    assert chunks == ["Verstappen wins! "]


@pytest.mark.asyncio
async def test_multiple_sentences_stream_separately():
    chunks = await _collect(["Yellow flag. ", "Car off at turn four."])
    assert chunks == ["Yellow flag. ", "Car off at turn four. "]


@pytest.mark.asyncio
async def test_soft_boundary_flushes_only_when_long_enough():
    # First comma comes after enough chars -> flush; downstream stays buffered.
    chunks = await _collect(["Leclerc leads,", " but Verstappen closes."])
    assert chunks[0] == "Leclerc leads, "
    assert chunks[-1] == "but Verstappen closes. "


@pytest.mark.asyncio
async def test_short_leading_clause_not_split():
    # "Go," is under min_chunk_chars, so it should not flush on the comma.
    chunks = await _collect(["Go,", " go, ", "Norris is through!"])
    assert chunks == ["Go, go, Norris is through! "]


@pytest.mark.asyncio
async def test_tail_without_punctuation_is_flushed():
    chunks = await _collect(["No punctuation here"])
    assert chunks == ["No punctuation here "]


@pytest.mark.asyncio
async def test_empty_stream_yields_nothing():
    chunks = await _collect([])
    assert chunks == []


@pytest.mark.asyncio
async def test_token_by_token_deltas_regroup_correctly():
    # Simulate a real token stream arriving one char at a time.
    line = "Hamilton pounces at the restart!"
    chunks = await _collect(list(line))
    assert "".join(chunks).strip() == line
