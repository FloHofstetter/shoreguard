# Proto Stubs

ShoreGuard communicates with OpenShell gateways over gRPC. The Python stubs are
generated from the upstream `.proto` files and checked into the repository so
that a local OpenShell checkout is not required for day-to-day development.

## When to regenerate

Re-run the generator whenever the OpenShell proto files change -- for example
after pulling a new OpenShell release that updates the API surface.

## Generating stubs

```bash
uv run python scripts/generate_proto.py /path/to/OpenShell/proto
```

The generated files are written to:

```
shoreguard/client/_proto/
```

## Linting and type checking exclusions

The generated stubs are **excluded** from both ruff and pyright. Their style and
type annotations are controlled by the protobuf compiler, not by our own
standards, so linting them would only produce noise.

## Source proto files

The canonical `.proto` definitions live in the
[OpenShell repository](https://github.com/NVIDIA/OpenShell). If you need to
inspect or modify the API contract, refer to the proto files there.
