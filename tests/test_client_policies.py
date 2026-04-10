"""Unit tests for PolicyManager — FakeStub pattern."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shoreguard.client._proto import openshell_pb2, sandbox_pb2
from shoreguard.client.policies import PolicyManager, _network_rule_to_dict, _policy_to_dict


class _FakeStub:
    def __init__(self) -> None:
        self.request = None

    def GetSandboxPolicyStatus(self, req, timeout=None):
        self.request = req
        rev = openshell_pb2.SandboxPolicyRevision(version=3, status=2, policy_hash="abc123")  # type: ignore[arg-type]
        return SimpleNamespace(active_version=3, revision=rev)

    def ListSandboxPolicies(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            revisions=[
                openshell_pb2.SandboxPolicyRevision(version=1, status=2, policy_hash="old"),  # type: ignore[arg-type]
                openshell_pb2.SandboxPolicyRevision(version=2, status=4, policy_hash="new"),  # type: ignore[arg-type]
            ]
        )

    def UpdateConfig(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(version=5, policy_hash="new-hash")

    def SubmitPolicyAnalysis(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            accepted_chunks=2,
            rejected_chunks=1,
            rejection_reasons=["conflicts with rule foo"],
        )


@pytest.fixture
def stub():
    return _FakeStub()


@pytest.fixture
def mgr(stub):
    m = object.__new__(PolicyManager)
    m._stub = stub
    m._timeout = 30.0
    return m


def test_get_sends_sandbox_name(mgr, stub):
    """get() sends sandbox name and returns revision dict."""
    result = mgr.get("sb1")

    assert stub.request.name == "sb1"
    assert result["active_version"] == 3
    assert result["revision"]["version"] == 3
    assert result["revision"]["status"] == "loaded"
    assert result["revision"]["policy_hash"] == "abc123"


def test_list_revisions_pagination(mgr, stub):
    """list_revisions() forwards limit/offset and returns list."""
    result = mgr.list_revisions("sb1", limit=5, offset=10)

    assert stub.request.name == "sb1"
    assert stub.request.limit == 5
    assert stub.request.offset == 10
    assert len(result) == 2
    assert result[0]["version"] == 1
    assert result[1]["status"] == "superseded"


def test_update_sends_proto(mgr, stub):
    """update() accepts a SandboxPolicy proto and returns version dict."""
    policy = sandbox_pb2.SandboxPolicy(version=4)

    result = mgr.update("sb1", policy)

    assert stub.request.name == "sb1"
    assert stub.request.policy == policy
    assert result["version"] == 5
    assert result["policy_hash"] == "new-hash"


def test_submit_analysis_forwards_summaries_and_chunks(mgr, stub):
    """submit_analysis() builds a proto request from plain dicts."""
    summaries = [
        {
            "sandbox_id": "sb1",
            "host": "api.example.com",
            "port": 443,
            "binary": "/usr/bin/curl",
            "deny_reason": "host not in allow list",
            "count": 3,
        },
    ]
    chunks = [
        {
            "id": "chunk-1",
            "rule_name": "allow_example_api",
            # proposed_rule is a nested NetworkPolicyRule — passing a dict
            # lets the proto constructor build it recursively.
            "proposed_rule": {
                "name": "allow_example_api",
                "endpoints": [{"host": "api.example.com", "port": 443}],
            },
            "rationale": "3 denials on same host in last hour",
            "confidence": 0.92,
        },
    ]

    result = mgr.submit_analysis(
        "sb1",
        summaries=summaries,
        proposed_chunks=chunks,
        analysis_mode="auto",
    )

    # Request built from dicts.
    assert stub.request.name == "sb1"
    assert stub.request.analysis_mode == "auto"
    assert len(stub.request.summaries) == 1
    assert stub.request.summaries[0].host == "api.example.com"
    assert stub.request.summaries[0].port == 443
    assert stub.request.summaries[0].binary == "/usr/bin/curl"
    assert len(stub.request.proposed_chunks) == 1
    assert stub.request.proposed_chunks[0].rule_name == "allow_example_api"
    assert stub.request.proposed_chunks[0].proposed_rule.name == "allow_example_api"
    assert stub.request.proposed_chunks[0].proposed_rule.endpoints[0].host == "api.example.com"

    # Response flattened to plain dict.
    assert result == {
        "accepted_chunks": 2,
        "rejected_chunks": 1,
        "rejection_reasons": ["conflicts with rule foo"],
    }


def test_submit_analysis_accepts_empty_lists(mgr, stub):
    """Empty summaries + proposed_chunks produce a valid empty request."""
    result = mgr.submit_analysis("sb1", summaries=[], proposed_chunks=[])

    assert stub.request.name == "sb1"
    assert list(stub.request.summaries) == []
    assert list(stub.request.proposed_chunks) == []
    assert result["accepted_chunks"] == 2  # fake stub always returns 2


def test_submit_analysis_raises_on_unknown_field(mgr):
    """Unknown dict keys surface as TypeError from the proto constructor."""
    with pytest.raises((ValueError, TypeError)):
        mgr.submit_analysis(
            "sb1",
            summaries=[{"bogus_field": "x"}],
            proposed_chunks=[],
        )


# ─── Proto → Dict conversion tests ──────────────────────────────────────────


def test_policy_to_dict_version_only():
    """Minimal policy with only version set."""
    policy = sandbox_pb2.SandboxPolicy(version=5)
    result = _policy_to_dict(policy)
    assert result == {"version": 5}


def test_policy_to_dict_filesystem():
    """Policy with filesystem section."""
    policy = sandbox_pb2.SandboxPolicy(
        version=3,
        filesystem=sandbox_pb2.FilesystemPolicy(
            include_workdir=True,
            read_only=["/usr", "/opt"],
            read_write=["/tmp"],
        ),
    )
    result = _policy_to_dict(policy)
    assert result["filesystem"]["include_workdir"] is True
    assert result["filesystem"]["read_only"] == ["/usr", "/opt"]
    assert result["filesystem"]["read_write"] == ["/tmp"]


def test_policy_to_dict_process():
    """Policy with process section."""
    policy = sandbox_pb2.SandboxPolicy(
        version=1,
        process=sandbox_pb2.ProcessPolicy(run_as_user="1000", run_as_group="1000"),
    )
    result = _policy_to_dict(policy)
    assert result["process"]["run_as_user"] == "1000"
    assert result["process"]["run_as_group"] == "1000"


def test_policy_to_dict_landlock():
    """Policy with landlock section."""
    policy = sandbox_pb2.SandboxPolicy(
        version=1,
        landlock=sandbox_pb2.LandlockPolicy(compatibility="best_effort"),
    )
    result = _policy_to_dict(policy)
    assert result["landlock"]["compatibility"] == "best_effort"


def test_policy_to_dict_network_policies():
    """Policy with network_policies map."""
    policy = sandbox_pb2.SandboxPolicy(version=2)
    rule = sandbox_pb2.NetworkPolicyRule(
        name="pypi",
        endpoints=[sandbox_pb2.NetworkEndpoint(host="pypi.org", port=443)],
    )
    policy.network_policies["pypi"].CopyFrom(rule)

    result = _policy_to_dict(policy)
    assert "pypi" in result["network_policies"]
    assert result["network_policies"]["pypi"]["name"] == "pypi"


def test_network_rule_to_dict_full():
    """NetworkPolicyRule with all endpoint fields."""
    rule = sandbox_pb2.NetworkPolicyRule(
        name="test-rule",
        endpoints=[
            sandbox_pb2.NetworkEndpoint(
                host="api.example.com",
                port=443,
                protocol="rest",
                tls="terminate",
                enforcement="enforce",
                access="full",
                allowed_ips=["1.2.3.4", "5.6.7.8"],
                ports=[443, 8443],
                rules=[
                    sandbox_pb2.L7Rule(
                        allow=sandbox_pb2.L7Allow(method="GET", path="/api/**", command=""),
                    ),
                ],
            ),
        ],
        binaries=[sandbox_pb2.NetworkBinary(path="/usr/bin/curl")],
    )
    result = _network_rule_to_dict(rule)

    assert result["name"] == "test-rule"
    assert len(result["endpoints"]) == 1
    ep = result["endpoints"][0]
    assert ep["host"] == "api.example.com"
    assert ep["port"] == 443
    assert ep["protocol"] == "rest"
    assert ep["tls"] == "terminate"
    assert ep["enforcement"] == "enforce"
    assert ep["access"] == "full"
    assert ep["allowed_ips"] == ["1.2.3.4", "5.6.7.8"]
    assert ep["ports"] == [443, 8443]
    assert len(ep["rules"]) == 1
    assert ep["rules"][0]["allow"]["method"] == "GET"
    assert ep["rules"][0]["allow"]["path"] == "/api/**"

    assert len(result["binaries"]) == 1
    assert result["binaries"][0]["path"] == "/usr/bin/curl"


def test_network_rule_to_dict_minimal():
    """NetworkPolicyRule with only required fields — optional fields omitted."""
    rule = sandbox_pb2.NetworkPolicyRule(
        name="minimal",
        endpoints=[sandbox_pb2.NetworkEndpoint(host="example.com", port=80)],
    )
    result = _network_rule_to_dict(rule)
    ep = result["endpoints"][0]
    assert ep["host"] == "example.com"
    assert ep["port"] == 80
    assert "protocol" not in ep
    assert "tls" not in ep


def test_get_with_embedded_policy(stub):
    """get() includes policy dict when revision has embedded policy."""
    policy = sandbox_pb2.SandboxPolicy(version=3)
    policy.network_policies["rule1"].CopyFrom(sandbox_pb2.NetworkPolicyRule(name="rule1"))

    class _StubWithPolicy(_FakeStub):
        def GetSandboxPolicyStatus(self, req, timeout=None):
            self.request = req
            rev = openshell_pb2.SandboxPolicyRevision(
                version=3,
                status=2,  # type: ignore[arg-type]
                policy_hash="abc",
                policy=policy,
            )
            return SimpleNamespace(active_version=3, revision=rev)

    s = _StubWithPolicy()
    m = object.__new__(PolicyManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    result = m.get("sb1")
    assert "policy" in result
    assert result["policy"]["version"] == 3
    assert "rule1" in result["policy"]["network_policies"]


# ─── Mutation-killing tests ──────────────────────────────────────────────────


def test_get_revision_timestamp_fields(stub):
    """Assert created_at_ms and loaded_at_ms are returned from get()."""

    class _StubWithTimestamps(_FakeStub):
        def GetSandboxPolicyStatus(self, req, timeout=None):
            self.request = req
            rev = openshell_pb2.SandboxPolicyRevision(
                version=1,
                status=2,  # type: ignore[arg-type]
                policy_hash="h",
                created_at_ms=111,
                loaded_at_ms=222,
            )
            return SimpleNamespace(active_version=1, revision=rev)

    s = _StubWithTimestamps()
    m = object.__new__(PolicyManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    result = m.get("sb1")
    assert result["revision"]["created_at_ms"] == 111
    assert result["revision"]["loaded_at_ms"] == 222


@pytest.mark.parametrize(
    "status_code,status_name",
    [
        (0, "unspecified"),
        (1, "pending"),
        (3, "failed"),
        (4, "superseded"),
    ],
)
def test_get_revision_status_codes(status_code, status_name):
    """get() maps different numeric status codes to correct names."""

    class _StubStatus(_FakeStub):
        def GetSandboxPolicyStatus(self, req, timeout=None):
            self.request = req
            rev = openshell_pb2.SandboxPolicyRevision(
                version=1,
                status=status_code,
                policy_hash="h",
            )
            return SimpleNamespace(active_version=1, revision=rev)

    s = _StubStatus()
    m = object.__new__(PolicyManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    result = m.get("sb1")
    assert result["revision"]["status"] == status_name


def test_list_revisions_load_error():
    """list_revisions() includes load_error field."""

    class _StubWithError(_FakeStub):
        def ListSandboxPolicies(self, req, timeout=None):
            self.request = req
            return SimpleNamespace(
                revisions=[
                    openshell_pb2.SandboxPolicyRevision(
                        version=1,
                        status=3,  # type: ignore[arg-type]
                        policy_hash="h",
                        created_at_ms=100,
                        loaded_at_ms=200,
                        load_error="parse error",
                    ),
                ]
            )

    s = _StubWithError()
    m = object.__new__(PolicyManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    result = m.list_revisions("sb1")
    assert result[0]["load_error"] == "parse error"
    assert result[0]["created_at_ms"] == 100
    assert result[0]["loaded_at_ms"] == 200


def test_update_global_scope():
    """update() forwards global_scope parameter."""

    class _StubGlobal(_FakeStub):
        def UpdateConfig(self, req, timeout=None):
            self.request = req
            # Capture the global field
            self.global_value = getattr(req, "global")
            return SimpleNamespace(version=5, policy_hash="h")

    s = _StubGlobal()
    m = object.__new__(PolicyManager)
    m._stub = s  # type: ignore[assignment]
    m._timeout = 30.0

    policy = sandbox_pb2.SandboxPolicy(version=1)
    m.update("sb1", policy, global_scope=True)
    assert s.global_value is True


def test_policy_to_dict_all_sections():
    """Policy with filesystem, process, landlock, and network_policies combined."""
    policy = sandbox_pb2.SandboxPolicy(
        version=7,
        filesystem=sandbox_pb2.FilesystemPolicy(
            include_workdir=False,
            read_only=["/usr"],
            read_write=["/tmp"],
        ),
        process=sandbox_pb2.ProcessPolicy(run_as_user="root", run_as_group="wheel"),
        landlock=sandbox_pb2.LandlockPolicy(compatibility="strict"),
    )
    rule = sandbox_pb2.NetworkPolicyRule(
        name="dns",
        endpoints=[sandbox_pb2.NetworkEndpoint(host="8.8.8.8", port=53)],
    )
    policy.network_policies["dns"].CopyFrom(rule)

    result = _policy_to_dict(policy)
    assert result["version"] == 7
    assert result["filesystem"]["include_workdir"] is False
    assert result["filesystem"]["read_only"] == ["/usr"]
    assert result["filesystem"]["read_write"] == ["/tmp"]
    assert result["process"]["run_as_user"] == "root"
    assert result["process"]["run_as_group"] == "wheel"
    assert result["landlock"]["compatibility"] == "strict"
    assert "dns" in result["network_policies"]
    assert result["network_policies"]["dns"]["name"] == "dns"


def test_policy_to_dict_empty_network_policies_omitted():
    """network_policies key is omitted when the map is empty."""
    policy = sandbox_pb2.SandboxPolicy(version=1)
    result = _policy_to_dict(policy)
    assert "network_policies" not in result


def test_network_rule_to_dict_empty():
    """NetworkPolicyRule with no endpoints and no binaries."""
    rule = sandbox_pb2.NetworkPolicyRule(name="empty-rule")
    result = _network_rule_to_dict(rule)
    assert result["name"] == "empty-rule"
    assert result["endpoints"] == []
    assert result["binaries"] == []


def test_network_rule_to_dict_endpoint_no_optional_fields():
    """Endpoint with only host/port — optional fields omitted from dict."""
    rule = sandbox_pb2.NetworkPolicyRule(
        name="basic",
        endpoints=[sandbox_pb2.NetworkEndpoint(host="example.com", port=80)],
    )
    result = _network_rule_to_dict(rule)
    ep = result["endpoints"][0]
    assert ep["host"] == "example.com"
    assert ep["port"] == 80
    for key in ("protocol", "tls", "enforcement", "access", "rules", "allowed_ips", "ports"):
        assert key not in ep


def test_network_rule_to_dict_l7_command():
    """L7 rule includes the command field."""
    rule = sandbox_pb2.NetworkPolicyRule(
        name="cmd-rule",
        endpoints=[
            sandbox_pb2.NetworkEndpoint(
                host="redis.local",
                port=6379,
                rules=[
                    sandbox_pb2.L7Rule(
                        allow=sandbox_pb2.L7Allow(method="", path="", command="GET"),
                    ),
                ],
            ),
        ],
    )
    result = _network_rule_to_dict(rule)
    assert result["endpoints"][0]["rules"][0]["allow"]["command"] == "GET"


# ─── Additional mutation-killing tests ──────────────────────────────────────


class TestPolicyStatusNamesMutations:
    """Kill mutations in POLICY_STATUS_NAMES dict."""

    @pytest.mark.parametrize(
        "code,expected",
        [
            (0, "unspecified"),
            (1, "pending"),
            (2, "loaded"),
            (3, "failed"),
            (4, "superseded"),
            (99, "unknown"),
        ],
    )
    def test_status_code_mapping(self, code, expected):
        from shoreguard.client.policies import POLICY_STATUS_NAMES

        assert POLICY_STATUS_NAMES.get(code, "unknown") == expected


class TestPolicyToDictMutations:
    """Kill mutations in _policy_to_dict."""

    def test_no_filesystem_no_key(self):
        policy = sandbox_pb2.SandboxPolicy(version=1)
        result = _policy_to_dict(policy)
        assert "filesystem" not in result

    def test_no_process_no_key(self):
        policy = sandbox_pb2.SandboxPolicy(version=1)
        result = _policy_to_dict(policy)
        assert "process" not in result

    def test_no_landlock_no_key(self):
        policy = sandbox_pb2.SandboxPolicy(version=1)
        result = _policy_to_dict(policy)
        assert "landlock" not in result

    def test_filesystem_include_workdir_true(self):
        policy = sandbox_pb2.SandboxPolicy(
            filesystem=sandbox_pb2.FilesystemPolicy(include_workdir=True)
        )
        result = _policy_to_dict(policy)
        assert result["filesystem"]["include_workdir"] is True

    def test_filesystem_include_workdir_false(self):
        policy = sandbox_pb2.SandboxPolicy(
            filesystem=sandbox_pb2.FilesystemPolicy(include_workdir=False)
        )
        result = _policy_to_dict(policy)
        assert result["filesystem"]["include_workdir"] is False

    def test_filesystem_read_only_list(self):
        policy = sandbox_pb2.SandboxPolicy(
            filesystem=sandbox_pb2.FilesystemPolicy(read_only=["/a", "/b"])
        )
        result = _policy_to_dict(policy)
        assert result["filesystem"]["read_only"] == ["/a", "/b"]

    def test_filesystem_read_write_list(self):
        policy = sandbox_pb2.SandboxPolicy(
            filesystem=sandbox_pb2.FilesystemPolicy(read_write=["/c"])
        )
        result = _policy_to_dict(policy)
        assert result["filesystem"]["read_write"] == ["/c"]


class TestNetworkRuleToDictMutations:
    """Kill mutations in _network_rule_to_dict."""

    def test_multiple_binaries(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            name="r",
            binaries=[
                sandbox_pb2.NetworkBinary(path="/a"),
                sandbox_pb2.NetworkBinary(path="/b"),
            ],
        )
        result = _network_rule_to_dict(rule)
        assert result["binaries"] == [{"path": "/a"}, {"path": "/b"}]

    def test_endpoint_protocol_included_when_set(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[sandbox_pb2.NetworkEndpoint(host="h", port=80, protocol="udp")]
        )
        result = _network_rule_to_dict(rule)
        assert result["endpoints"][0]["protocol"] == "udp"

    def test_endpoint_tls_included_when_set(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[sandbox_pb2.NetworkEndpoint(host="h", port=443, tls="terminate")]
        )
        result = _network_rule_to_dict(rule)
        assert result["endpoints"][0]["tls"] == "terminate"

    def test_endpoint_enforcement_included_when_set(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[sandbox_pb2.NetworkEndpoint(host="h", port=80, enforcement="block")]
        )
        result = _network_rule_to_dict(rule)
        assert result["endpoints"][0]["enforcement"] == "block"

    def test_endpoint_access_included_when_set(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[sandbox_pb2.NetworkEndpoint(host="h", port=80, access="allow")]
        )
        result = _network_rule_to_dict(rule)
        assert result["endpoints"][0]["access"] == "allow"

    def test_endpoint_allowed_ips_included_when_set(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[sandbox_pb2.NetworkEndpoint(host="h", port=80, allowed_ips=["10.0.0.1"])]
        )
        result = _network_rule_to_dict(rule)
        assert result["endpoints"][0]["allowed_ips"] == ["10.0.0.1"]

    def test_endpoint_ports_included_when_set(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[sandbox_pb2.NetworkEndpoint(host="h", port=80, ports=[80, 8080])]
        )
        result = _network_rule_to_dict(rule)
        assert result["endpoints"][0]["ports"] == [80, 8080]

    def test_multiple_endpoints(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            name="r",
            endpoints=[
                sandbox_pb2.NetworkEndpoint(host="a", port=80),
                sandbox_pb2.NetworkEndpoint(host="b", port=443),
            ],
        )
        result = _network_rule_to_dict(rule)
        assert len(result["endpoints"]) == 2
        assert result["endpoints"][0]["host"] == "a"
        assert result["endpoints"][1]["host"] == "b"

    def test_l7_allow_method_path_command(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[
                sandbox_pb2.NetworkEndpoint(
                    host="h",
                    port=80,
                    rules=[
                        sandbox_pb2.L7Rule(
                            allow=sandbox_pb2.L7Allow(method="M", path="P", command="C")
                        )
                    ],
                )
            ],
        )
        result = _network_rule_to_dict(rule)
        allow = result["endpoints"][0]["rules"][0]["allow"]
        assert allow["method"] == "M"
        assert allow["path"] == "P"
        assert allow["command"] == "C"

    def test_l7_query_glob_in_result(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[
                sandbox_pb2.NetworkEndpoint(
                    host="h",
                    port=80,
                    rules=[sandbox_pb2.L7Rule(allow=sandbox_pb2.L7Allow(method="GET", path="/"))],
                )
            ],
        )
        # Add query matcher
        rule.endpoints[0].rules[0].allow.query["param"].CopyFrom(
            sandbox_pb2.L7QueryMatcher(glob="val*")
        )
        result = _network_rule_to_dict(rule)
        allow = result["endpoints"][0]["rules"][0]["allow"]
        assert "query" in allow
        assert allow["query"]["param"]["glob"] == "val*"

    def test_l7_query_any_in_result(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[
                sandbox_pb2.NetworkEndpoint(
                    host="h",
                    port=80,
                    rules=[sandbox_pb2.L7Rule(allow=sandbox_pb2.L7Allow(method="GET", path="/"))],
                )
            ],
        )
        rule.endpoints[0].rules[0].allow.query["p"].CopyFrom(
            sandbox_pb2.L7QueryMatcher(**{"any": ["a", "b"]})  # type: ignore[arg-type]
        )
        result = _network_rule_to_dict(rule)
        assert result["endpoints"][0]["rules"][0]["allow"]["query"]["p"]["any"] == ["a", "b"]

    def test_l7_query_glob_empty_not_included(self):
        """When glob is empty string, it should not appear in query dict."""
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[
                sandbox_pb2.NetworkEndpoint(
                    host="h",
                    port=80,
                    rules=[sandbox_pb2.L7Rule(allow=sandbox_pb2.L7Allow(method="GET", path="/"))],
                )
            ],
        )
        rule.endpoints[0].rules[0].allow.query["p"].CopyFrom(
            sandbox_pb2.L7QueryMatcher(glob="", **{"any": ["x"]})
        )
        result = _network_rule_to_dict(rule)
        q = result["endpoints"][0]["rules"][0]["allow"]["query"]["p"]
        assert "glob" not in q
        assert q["any"] == ["x"]

    def test_l7_query_any_empty_not_included(self):
        """When any is empty list, it should not appear in query dict."""
        rule = sandbox_pb2.NetworkPolicyRule(
            endpoints=[
                sandbox_pb2.NetworkEndpoint(
                    host="h",
                    port=80,
                    rules=[sandbox_pb2.L7Rule(allow=sandbox_pb2.L7Allow(method="GET", path="/"))],
                )
            ],
        )
        rule.endpoints[0].rules[0].allow.query["p"].CopyFrom(sandbox_pb2.L7QueryMatcher(glob="g"))
        result = _network_rule_to_dict(rule)
        q = result["endpoints"][0]["rules"][0]["allow"]["query"]["p"]
        assert q["glob"] == "g"
        assert "any" not in q


class TestPolicyManagerMutations:
    """Kill mutations in PolicyManager method arg passing."""

    def test_get_uses_timeout(self):
        class _Stub(_FakeStub):
            def GetSandboxPolicyStatus(self, req, timeout=None):
                self.timeout = timeout
                rev = openshell_pb2.SandboxPolicyRevision(version=1, status=2, policy_hash="h")  # type: ignore[arg-type]
                return SimpleNamespace(active_version=1, revision=rev)

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 55.0
        m.get("sb")
        assert s.timeout == 55.0

    def test_get_version_sends_version(self):
        class _Stub(_FakeStub):
            def GetSandboxPolicyStatus(self, req, timeout=None):
                self.request = req
                rev = openshell_pb2.SandboxPolicyRevision(version=3, status=2, policy_hash="h")  # type: ignore[arg-type]
                return SimpleNamespace(active_version=3, revision=rev)

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.get_version("sb", 3)
        assert s.request.name == "sb"
        assert s.request.version == 3

    def test_get_version_returns_correct_structure(self):
        class _Stub(_FakeStub):
            def GetSandboxPolicyStatus(self, req, timeout=None):
                rev = openshell_pb2.SandboxPolicyRevision(
                    version=5,
                    status=1,  # type: ignore[arg-type]
                    policy_hash="hash5",
                    created_at_ms=100,
                    loaded_at_ms=200,
                )
                return SimpleNamespace(active_version=5, revision=rev)

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.get_version("sb", 5)
        assert result["active_version"] == 5
        assert result["revision"]["version"] == 5
        assert result["revision"]["status"] == "pending"
        assert result["revision"]["policy_hash"] == "hash5"
        assert result["revision"]["created_at_ms"] == 100
        assert result["revision"]["loaded_at_ms"] == 200

    def test_list_revisions_default_params(self):
        class _Stub(_FakeStub):
            def ListSandboxPolicies(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(revisions=[])

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.list_revisions("sb")
        assert s.request.limit == 20
        assert s.request.offset == 0

    def test_list_revisions_all_fields(self):
        class _Stub(_FakeStub):
            def ListSandboxPolicies(self, req, timeout=None):
                return SimpleNamespace(
                    revisions=[
                        openshell_pb2.SandboxPolicyRevision(
                            version=1,
                            status=2,  # type: ignore[arg-type]
                            policy_hash="h1",
                            created_at_ms=10,
                            loaded_at_ms=20,
                            load_error="",
                        ),
                    ]
                )

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.list_revisions("sb")
        assert result[0] == {
            "version": 1,
            "status": "loaded",
            "policy_hash": "h1",
            "created_at_ms": 10,
            "loaded_at_ms": 20,
            "load_error": "",
        }

    def test_update_returns_exact_dict(self):
        class _Stub(_FakeStub):
            def UpdateConfig(self, req, timeout=None):
                return SimpleNamespace(version=99, policy_hash="H99")

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.update("sb", sandbox_pb2.SandboxPolicy(version=1))
        assert result == {"version": 99, "policy_hash": "H99"}

    def test_update_default_global_scope_false(self):
        class _Stub(_FakeStub):
            def UpdateConfig(self, req, timeout=None):
                self.request = req
                self.global_value = getattr(req, "global")
                return SimpleNamespace(version=1, policy_hash="h")

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.update("sb", sandbox_pb2.SandboxPolicy(version=1))
        assert s.global_value is False

    def test_get_version_with_embedded_policy(self):
        policy = sandbox_pb2.SandboxPolicy(version=2)
        policy.network_policies["r"].CopyFrom(sandbox_pb2.NetworkPolicyRule(name="r"))

        class _Stub(_FakeStub):
            def GetSandboxPolicyStatus(self, req, timeout=None):
                rev = openshell_pb2.SandboxPolicyRevision(
                    version=2,
                    status=2,  # type: ignore[arg-type]
                    policy_hash="h",
                    policy=policy,
                )
                return SimpleNamespace(active_version=2, revision=rev)

        s = _Stub()
        m = object.__new__(PolicyManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.get_version("sb", 2)
        assert "policy" in result
        assert result["policy"]["version"] == 2
        assert "r" in result["policy"]["network_policies"]
