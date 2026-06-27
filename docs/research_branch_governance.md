# Research Branch Governance

`P1-opus` and related research branches are shadow-only by default. They may
read market data, run replay validation, and produce diagnostics, but they must
not send broker writes.

Live execution remains isolated behind the execution guard and broker gateway.
Any `order_send` path must stay in the approved gateway boundary, with research
branches denied unless explicitly moved out of shadow-only governance by a human
operator.
