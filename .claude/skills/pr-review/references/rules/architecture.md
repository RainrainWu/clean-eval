## Architecture

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| CA-01 | blocker | Low | **Dependency rule.** Code in an inner layer must not import from an outer layer (e.g. domain → infrastructure, application → interface). |
| CA-02 | major | Low | **Pure domain.** Domain code must not perform I/O, read env/config, call clocks/randomness directly, or depend on frameworks (ORM, HTTP, cloud SDKs). |
| CA-03 | major | Moderate | **Ports over concretions.** Use cases depend on interfaces/protocols they own and call the instance injected at the composition root; they never construct or import a concrete adapter. |
| CA-04 | major | Moderate | **Thin adapters.** Controllers, handlers and presenters translate and delegate; business decisions (branching on domain rules) belong in domain/application. |
| CA-05 | major | Moderate | **Module boundaries.** Cross-module access goes through the other module's public API/entrypoint, not its internals. New cross-module imports need justification. |
| CA-06 | minor | High | **Boundary models.** Persistence/transport schemas (ORM rows, request DTOs, SDK responses) are mapped to domain types at the boundary and don't leak inward. |
| CA-07 | blocker | Low | **Declared utilities.** Use cases reach every external utility (processes, files, network, SDKs, the clock, the environment) through an interface they own, never by a direct call, and know none of a tool's commands, formats or conventions. |
| CA-08 | blocker | Low | **Faithful implementations.** An implementation obeys its interface: the signature, and the results and errors its contract states. |
| CA-09 | blocker | Low | **Gateways only serve.** A gateway or repository wraps its tool (calls it, maps its data and errors) and decides nothing: business logic never lives there. |
| CA-10 | minor | Moderate | **Placement.** New files and modules follow the repository's existing structure and naming; one that fits nowhere says why. |
