"""The adversarial corpus: injection payloads, and proof they stay inert.

Required by `REQ-SEC-014 AC-3` and `REQ-DOC-009 AC-3`, and specified since Phase
0. Every test in here is marked `adversarial` and runs on every release.

**These are not unit tests of the trust types.** `packages/scrapr_core/tests/
security/test_trust.py` covers those. What lives here is the end of the pipe:
real payloads, carried through the real path a document or a web page takes, with
an assertion about where the payload ended up — in the untrusted half of a model
request, in an evidence row, and never in an instruction, a tool name or a
system policy.

The distinction matters because the defence is structural. A test that only
asserts `Untrusted.__str__` raises proves the type works; it does not prove
anybody used it.
"""
