"""The model designer's IR: shapes, parameter counts, and generated PyTorch.

The generator has no independent specification — the only thing that makes its
output trustworthy is that it is checked from several directions at once: the
source parses and compiles, the shapes it implies agree with the arithmetic
`shapes.py` does separately, the parameter counts match hand-computed ones, and the
same graph produces the same bytes every run. Together those are the correctness
argument; individually none of them is.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from backend.modules.interpretability import agent_tools
from backend.modules.interpretability.graph import (
    codegen,
    examples,
    generate,
    handoff,
    importer,
    infer,
    parse,
    probe,
    spec,
    store,
    tracer,
)
from backend.modules.interpretability.graph.models import (
    DesignGraph,
    GraphEdge,
    GraphNode,
    SubGraph,
)
from backend.modules.interpretability.models import (
    AttentionSpec,
    FfnSpec,
    ModelArchitecture,
    MoeSpec,
)
from backend.modules.interpretability.graph.walk import CycleError, topo_order
from backend.modules.training.models import ProjectModel

TEMPLATES = ("llama", "gpt", "moe")


def node(nid: str, ntype: str, **params: object) -> GraphNode:
    return GraphNode(id=nid, type=ntype, params=params)


def edge(source: str, target: str, handle: str = "in") -> GraphEdge:
    return GraphEdge(
        id=f"{source}->{target}", source=source, target=target, targetHandle=handle
    )


def linear_graph(**config: object) -> DesignGraph:
    """Input → embedding → norm → head → output, with no groups. The minimum model."""
    return DesignGraph(
        name="Tiny",
        config={"vocab_size": 100, "d_model": 16, **config},  # type: ignore[arg-type]
        nodes=[
            node("i", "io.input"),
            node("e", "embed.token"),
            node("n", "norm.rms"),
            node("h", "ffn.linear", dim="$d_model", out_features="$vocab_size"),
            node("o", "io.output"),
        ],
        edges=[edge("i", "e"), edge("e", "n"), edge("n", "h"), edge("h", "o")],
    )


# ── the catalog ──────────────────────────────────────────────────────────────────


def test_every_node_type_can_be_rendered_and_emitted() -> None:
    """A node the palette offers but codegen cannot emit is a dead end in the UI.

    `group` is the one exception: it has no single emitted form, because what it
    emits depends on a subgraph the spec table cannot see.
    """
    for entry in spec.catalog():
        current = spec.SPECS[entry["type"]]
        assert current.doc, f"{current.type} has no description for the palette"
        # `group` and the four terminals are the exceptions, and for the same
        # reason: what they emit depends on the scope they sit in, which a flat
        # spec table cannot see. Codegen handles them by name.
        if current.type == "group" or current.category == "io":
            continue
        assert current.shape_fn is not None, f"{current.type} cannot report a shape"
        assert current.forward_fn is not None, f"{current.type} cannot be emitted"
        for param in current.params:
            assert param.help, f"{current.type}.{param.name} has no help text"


def test_catalog_is_serialisable_without_callables() -> None:
    entry = next(e for e in spec.catalog() if e["type"] == "attn.mha")
    assert entry["inputs"] == [
        {"name": "in", "type": "tensor", "multi": False, "label": "in"}
    ]
    assert {p["name"] for p in entry["params"]} >= {
        "heads",
        "kv_heads",
        "causal",
        "rope",
    }


# ── templates ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_infer_cleanly(name: str) -> None:
    report = infer(examples.template(name))
    assert report.issues == []
    assert report.ok
    assert report.totalParams > 0


@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_generate_compiling_source(name: str) -> None:
    result = generate(examples.template(name))
    assert result.error is None
    compile(result.source, f"{name}.py", "exec")


@pytest.mark.parametrize("name", TEMPLATES)
def test_generated_source_is_deterministic(name: str) -> None:
    """Same graph, same bytes — golden fixtures and round-tripping both depend on it."""
    assert (
        generate(examples.template(name)).source
        == generate(examples.template(name)).source
    )


@pytest.mark.parametrize("name", TEMPLATES)
def test_every_marker_names_a_real_node(name: str) -> None:
    """The line→node map is the round-trip hinge; a marker for a node that no longer
    exists would silently reattach an edit to nothing."""
    graph = examples.template(name)
    ids = {n.id for n in graph.nodes} | {n.id for g in graph.groups for n in g.nodes}
    result = generate(graph)
    assert result.markers
    assert set(result.markers.values()) <= ids
    rows = result.source.splitlines()
    for line, nid in result.markers.items():
        assert codegen.marker_of(rows[line - 1]) == nid


def test_the_stack_is_a_loop_not_forty_copies() -> None:
    source = generate(examples.llama_small()).source
    assert "nn.ModuleList(" in source
    # The class definition, and exactly one instantiation feeding the loop.
    assert source.count("DecoderBlock(") == 2
    assert "for _layer in" in source


def test_config_stays_parametric() -> None:
    """`d_model` reaches the generated class as a keyword argument, not as 512.

    A graph that hard-codes today's width is a single model; one that keeps the
    variable is a family, which is the entire reason config references exist.
    """
    source = generate(examples.llama_small()).source
    assert "d_model: int = 512" in source
    assert "RMSNorm(d_model" in source
    assert "RMSNorm(512" not in source


# ── shapes ───────────────────────────────────────────────────────────────────────


def test_shapes_stay_symbolic_in_batch_and_sequence() -> None:
    report = infer(linear_graph())
    assert report.shapes["e"]["out"] == ["B", "T", 16]
    assert report.shapes["h"]["out"] == ["B", "T", 100]


def test_a_width_mismatch_is_located_not_just_reported() -> None:
    """A message with no node id cannot become a red socket, which is the whole
    point of inferring shapes in the first place."""
    graph = linear_graph()
    graph.nodes[2].params["dim"] = (
        8  # the norm claims a width the embedding does not produce
    )
    report = infer(graph)
    assert not report.ok
    assert [i.nodeId for i in report.issues] == ["n"]
    assert "8" in report.issues[0].message


def test_parameter_count_matches_hand_arithmetic() -> None:
    report = infer(linear_graph())
    assert report.params["e"] == 100 * 16  # embedding table
    assert report.params["n"] == 16  # one gain per feature
    assert report.params["h"] == 16 * 100  # the head, biasless
    assert report.totalParams == 100 * 16 + 16 + 16 * 100


def test_grouped_query_attention_costs_less_than_multi_head() -> None:
    """The KV-head ratio is the single biggest driver of cost, so it had better be
    visible in the numbers the cost overlay shows."""
    graph = examples.llama_small()
    gqa = infer(graph).totalParams
    for candidate in graph.groups[0].nodes:
        if candidate.type == "attn.mha":
            candidate.params["kv_heads"] = "$n_heads"
    assert infer(graph).totalParams > gqa


def test_a_stack_refuses_a_block_that_changes_shape() -> None:
    """Each copy is fed the previous one's output, so a block that widens its input
    can only run once. Emitting the loop anyway fails on the second iteration —
    minutes into a run, with a message about matrix shapes."""
    graph = examples.llama_small()
    block = graph.groups[0]
    widen = node("widen", "ffn.linear", dim="$d_model", out_features=64)
    block.nodes.append(widen)
    block.edges = [e for e in block.edges if e.target != "blk_gout"]
    block.edges += [edge("blk_res2", "widen"), edge("widen", "blk_gout")]
    report = infer(graph)
    assert not report.ok
    assert any("stacked" in i.message for i in report.issues)


def test_a_failure_inside_a_group_is_reported_once() -> None:
    """A node inside a block failing must not also make the block claim its output
    is unwired. It is wired; the chain feeding it broke. Two explanations, one of
    them false, is worse than one — the reader chases the wrong one."""
    graph = examples.llama_small()
    graph.config["n_heads"] = 7  # 7 is not a whole multiple of 2 KV heads
    report = infer(graph)
    assert not report.ok
    assert [i.nodeId for i in report.issues] == ["blk_attn"]
    assert "kv_heads" in report.issues[0].message


def test_a_group_with_a_genuinely_unwired_output_still_says_so() -> None:
    graph = examples.llama_small()
    block = graph.groups[0]
    block.edges = [e for e in block.edges if e.target != "blk_gout"]
    report = infer(graph)
    assert not report.ok
    assert any("nothing connected to its output" in i.message for i in report.issues)


def test_a_group_nothing_instantiates_is_still_labelled() -> None:
    """A group you are part-way through building is not wired into the model yet.
    Leaving its wires unlabelled would make the canvas useless for exactly the job
    of building one."""
    graph = examples.llama_small()
    spare = SubGraph(
        id="spare",
        name="Spare",
        nodes=[
            node("sp_gin", "io.group_input"),
            node("sp_norm", "norm.rms"),
            node("sp_gout", "io.group_output"),
        ],
        edges=[edge("sp_gin", "sp_norm"), edge("sp_norm", "sp_gout")],
    )
    graph.groups.append(spare)
    report = infer(graph)
    assert report.ok
    assert report.shapes["sp_norm"]["out"] == ["B", "T", 512]


def test_an_uninstantiated_group_adds_no_parameters_to_the_total() -> None:
    """A class the model never instantiates holds no weights. Counting its
    parameters would inflate the headline number by a block that does not run."""
    graph = examples.llama_small()
    before = infer(graph).totalParams
    graph.groups.append(
        SubGraph(
            id="spare",
            name="Spare",
            nodes=[
                node("sp_gin", "io.group_input"),
                node("sp_lin", "ffn.linear", dim="$d_model", out_features="$d_model"),
                node("sp_gout", "io.group_output"),
            ],
            edges=[edge("sp_gin", "sp_lin"), edge("sp_lin", "sp_gout")],
        )
    )
    report = infer(graph)
    assert report.totalParams == before
    # Still reported per node — that is what the layer you are editing holds — just
    # not added to a model that does not contain it.
    assert report.params["sp_lin"] > 0


def test_two_groups_generating_one_class_are_refused() -> None:
    """`class_name` strips punctuation, so "Block 1" and "Block-1" are one class and
    the second definition silently replaces the first — every instance of the first
    would then run the second's code, with no error anywhere."""
    graph = examples.llama_small()
    graph.groups.append(
        SubGraph(id="twin", name=f"{graph.groups[0].name}!", nodes=[], edges=[])
    )
    result = generate(graph)
    assert result.error is not None
    assert "silently replaces" in result.error


def test_a_group_named_after_the_model_is_refused() -> None:
    graph = examples.llama_small()
    graph.groups[0].name = graph.name
    result = generate(graph)
    assert result.error is not None
    assert "would replace" in result.error


def test_a_missing_residual_input_is_an_error_not_a_silent_sum() -> None:
    graph = examples.llama_small()
    block = graph.groups[0]
    block.edges = [
        e for e in block.edges if not (e.source == "blk_gin" and e.target == "blk_res1")
    ]
    report = infer(graph)
    assert not report.ok
    assert any(i.nodeId == "blk_res1" for i in report.issues)


def test_a_cycle_is_refused_rather_than_unrolled() -> None:
    graph = linear_graph()
    graph.edges.append(edge("h", "n"))
    report = infer(graph)
    assert not report.ok
    assert "cycle" in report.issues[0].message
    with pytest.raises(CycleError):
        topo_order(graph.nodes, graph.edges)


def test_an_empty_graph_warns_rather_than_erroring() -> None:
    """Mid-edit is the normal state of a canvas. A blank one is not a failure."""
    report = infer(DesignGraph(name="Empty"))
    assert report.ok
    assert {i.severity for i in report.issues} == {"warning"}


# ── mute is ablation ─────────────────────────────────────────────────────────────


def test_muting_a_node_removes_its_code_and_its_parameters() -> None:
    """Blender's mute passes the input straight through. Here that is an ablation,
    so the muted layer must vanish from the emitted forward pass *and* stop being
    counted — a count that survives the ablation would describe a different model."""
    graph = examples.llama_small()
    full = infer(graph).totalParams
    ffn = next(n for n in graph.groups[0].nodes if n.type == "ffn.swiglu")
    ffn.muted = True

    report = infer(graph)
    assert report.ok
    assert report.totalParams < full
    assert "blk_ffn" not in report.params

    source = generate(graph).source
    assert "SwiGLU" not in source
    assert "self.ffn" not in source
    # The residual around it survives — muting one node ablates that node, not the
    # branch it sat on, which is what makes the comparison meaningful.
    assert "horrible:node=blk_res2" in source


def test_muting_preserves_the_wire() -> None:
    graph = linear_graph()
    graph.nodes[2].muted = True
    report = infer(graph)
    assert report.ok
    assert report.shapes["n"]["out"] == ["B", "T", 16]


# ── codegen failure modes ────────────────────────────────────────────────────────


def test_a_graph_with_no_output_reports_why_instead_of_emitting_half_a_class() -> None:
    graph = linear_graph()
    graph.nodes = [n for n in graph.nodes if n.type != "io.output"]
    graph.edges = [e for e in graph.edges if e.target != "o"]
    result = generate(graph)
    assert result.source == ""
    assert result.error and "Output" in result.error


def test_a_self_containing_group_is_refused() -> None:
    """Blender forbids recursive node groups for the same reason: there is no
    terminating condition, so the generator would recurse until it died."""
    graph = examples.llama_small()
    block = graph.groups[0]
    block.nodes.append(node("inner", "group", group=block.id, count=1))
    block.edges = [e for e in block.edges if e.target != "blk_gout"]
    block.edges += [edge("blk_res2", "inner"), edge("inner", "blk_gout")]
    result = generate(graph)
    assert result.error and "itself" in result.error


def test_class_names_survive_hostile_input() -> None:
    assert codegen.class_name("my model 2") == "MyModel2"
    assert codegen.class_name("") == "Module"
    assert codegen.class_name("class") == "Class"  # capitalised, so no longer a keyword
    assert codegen.class_name("None") == "ModuleNone"
    assert codegen.class_name("9lives") == "Module9lives"


def test_generated_source_defines_only_what_it_uses() -> None:
    """Primitives are emitted on demand: a dense-MLP model should not carry a
    SwiGLU it never instantiates, and a model with no MoE should not carry a router."""
    gpt = generate(examples.gpt_small()).source
    assert "class MLP" in gpt
    assert "class MoE" not in gpt
    tree = ast.parse(gpt)
    defined = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert "SwiGLU" not in defined


def test_primitives_are_emitted_before_they_are_referenced() -> None:
    """MoE builds SwiGLU experts; SwiGLU defined afterwards is a NameError at import,
    which reads as 'the generated code is broken' rather than 'ordered wrongly'."""
    source = generate(examples.moe_small()).source
    assert source.index("class SwiGLU") < source.index("class MoE")
    assert source.index("class RotaryEmbedding") < source.index(
        "class MultiHeadAttention"
    )


# ── round trip (graph ⇄ code) ────────────────────────────────────────────────────


@pytest.mark.parametrize("name", TEMPLATES)
def test_the_generated_file_is_a_fixed_point(name: str) -> None:
    """`emit(parse(emit(g))) == emit(g)`, byte for byte.

    This is the guarantee that matters more than IR equality: parse a file, regenerate
    it, and nothing churns. It is what a user experiences when they edit the code pane
    and hit save — a round trip that reformatted the file, renumbered its locals or
    renamed its attributes would make every save look like a change.
    """
    source = generate(examples.template(name)).source
    assert generate(parse.parse_module(source).graph).source == source


@pytest.mark.parametrize("name", TEMPLATES)
def test_node_ids_survive_the_round_trip(name: str) -> None:
    """Recovered from the markers — which is what lets the layout sidecar still
    describe the graph, instead of the canvas being re-arranged on every save."""
    graph = examples.template(name)
    parsed = parse.parse_module(generate(graph).source).graph
    original = {n.id for n in graph.nodes if n.type not in ("io.input", "io.output")}
    recovered = {n.id for n in parsed.nodes}
    assert original <= recovered


def test_an_edit_in_the_code_lands_on_the_graph() -> None:
    """The actual point of the feature: change a number in the source and the node
    carries it."""
    source = generate(examples.llama_small()).source.replace(
        "heads=n_heads", "heads=16"
    )
    parsed = parse.parse_module(source).graph
    attn = next(n for n in parsed.groups[0].nodes if n.type == "attn.mha")
    assert attn.params["heads"] == 16


def test_config_comes_back_from_the_init_signature() -> None:
    parsed = parse.parse_module(generate(examples.llama_small()).source).graph
    assert parsed.config["d_model"] == 512
    assert parsed.config["n_layers"] == 8
    # And the reference survives as a reference, not as the number it resolves to —
    # collapsing it would turn a family of models into one.
    norm = next(n for n in parsed.nodes if n.type == "norm.rms")
    assert norm.params["dim"] == "$d_model"


def test_source_we_cannot_map_is_preserved_rather_than_dropped() -> None:
    """The load-bearing rule. A class the parser does not understand becomes a
    `custom.module` holding its source verbatim, and the caller is told which — an
    opaque import nobody is told about is indistinguishable from a wrong one."""
    graph = examples.llama_small()
    block = graph.groups[0]
    body = "class Mystery(nn.Module):\n    def forward(self, x):\n        return x * 2"
    block.nodes.append(
        GraphNode(
            id="mystery",
            type="custom.module",
            params={"class_name": "Mystery", "code": body, "args": ""},
        )
    )
    block.edges = [e for e in block.edges if e.target != "blk_gout"]
    block.edges += [edge("blk_res2", "mystery"), edge("mystery", "blk_gout")]

    result = parse.parse_module(generate(graph).source)
    assert result.opaque == ["Mystery"]
    node = next(n for n in result.graph.groups[0].nodes if n.type == "custom.module")
    assert "return x * 2" in str(node.params["code"])


def test_a_file_we_cannot_read_is_refused_whole() -> None:
    """Never a partial import. Half a graph silently replacing a whole one is the
    worst outcome available here."""
    with pytest.raises(parse.ParseError):
        parse.parse_module("class Broken(nn.Module)\n    pass")
    with pytest.raises(parse.ParseError):
        parse.parse_module("x = 1\n")


def test_every_constructor_the_generator_emits_can_be_read_back() -> None:
    """`CONSTRUCTORS` mirrors `init_fn`, because a string cannot be run backwards.

    A node type added to the catalog but not to the mirror is not a crash — it is a
    node that silently becomes an opaque custom module on the way back in, which is
    exactly the kind of quiet degradation this suite exists to catch.
    """
    emitted = {
        s.type
        for s in spec.SPECS.values()
        if s.init_fn is not None and s.type not in ("custom.module", "group")
    }
    readable = {node_type for node_type, _ in parse.CONSTRUCTORS.values()}
    # Both embeddings share `nn.Embedding`; `forward` is what tells them apart.
    readable.add("embed.learned_positional")
    assert emitted <= readable, f"no way to parse back: {sorted(emitted - readable)}"


# ── tier 2: does the generated code actually run? ────────────────────────────────


@pytest.mark.parametrize("name", TEMPLATES)
def test_the_generated_module_runs_and_the_estimate_is_right(name: str) -> None:
    """The one test that can turn an estimate into a measurement.

    Everything else here checks our arithmetic against itself. This builds the emitted
    module in real PyTorch, runs a forward pass, and compares `sum(p.numel())` against
    what `shapes.py` predicted — which is the number the cost overlay shows on every
    keystroke. If they ever diverge, the pane has been quietly lying and `shapes.py` is
    what needs fixing, not this assertion.

    Skipped when torch is absent, because the backend deliberately does not depend on
    it: heavy dependencies live in per-project uv envs, and in production this runs as
    a subprocess in one (`probe.py`).
    """
    torch = pytest.importorskip(
        "torch", reason="tier-2 validation needs torch; tier 1 is torch-free by design"
    )

    import importlib.util
    import tempfile
    from pathlib import Path

    graph = examples.template(name)
    estimate = infer(graph)
    source = generate(graph).source

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "design_under_test.py"
        path.write_text(source, encoding="utf-8")
        spec_ = importlib.util.spec_from_file_location("design_under_test", path)
        assert spec_ and spec_.loader
        module = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(module)
        model = getattr(module, codegen.class_name(graph.name))()

    vocab = int(graph.config["vocab_size"])
    ids = torch.randint(0, vocab, (2, 8))
    with torch.no_grad():
        out = model(ids)

    assert list(out.shape) == [2, 8, vocab]
    assert sum(p.numel() for p in model.parameters()) == estimate.totalParams

    # And the same measurement, resolved back onto individual nodes. A total that
    # agrees can still hide two nodes wrong in opposite directions, which is exactly
    # what the per-node overlay exists to catch.
    leaves = {
        path: sum(p.numel() for p in sub.parameters(recurse=False))
        for path, sub in model.named_modules()
    }
    emitted = generate(graph)
    per_node = probe.attribute_params(
        {k: v for k, v in leaves.items() if v},
        emitted.attrs,
        emitted.attrClasses,
        emitted.rootClass,
    )
    assert per_node, "no measured module resolved back to a node"
    assert sum(per_node.values()) == estimate.totalParams
    for nid, measured in per_node.items():
        assert measured == estimate.params[nid], f"{nid} was estimated wrong"


# ── importing an inspected model ─────────────────────────────────────────────────


def _llama_metadata(**over: object) -> ModelArchitecture:
    """Metadata as complete as a good GGUF actually gets."""
    base = dict(
        source="ollama",
        model="llama-3.2-3b-instruct",
        hiddenSize=3072,
        layers=28,
        vocabSize=128256,
        normType="rmsnorm",
        attention=AttentionSpec(
            heads=24, kvHeads=8, headDim=128, headDimDerived=True, ropeTheta=500000.0
        ),
        ffn=FfnSpec(intermediateSize=8192, activation="silu", gated=True),
    )
    base.update(over)
    return ModelArchitecture(**base)  # type: ignore[arg-type]


def test_a_complete_model_imports_with_nothing_assumed() -> None:
    result = importer.from_architecture(_llama_metadata())
    assert result.error is None
    assert result.assumed == []
    assert result.graph is not None
    assert result.graph.config["d_model"] == 3072
    assert result.graph.config["n_kv_heads"] == 8


def test_an_imported_model_generates_compiling_source() -> None:
    result = importer.from_architecture(_llama_metadata())
    assert result.graph is not None
    code = generate(result.graph)
    assert code.error is None
    ast.parse(code.source)


def test_an_imported_model_has_no_shape_problems() -> None:
    result = importer.from_architecture(_llama_metadata())
    assert result.graph is not None
    assert infer(result.graph).ok


@pytest.mark.parametrize(
    "field,label",
    [
        ("hiddenSize", "hidden size"),
        ("layers", "layer count"),
        ("vocabSize", "vocabulary size"),
    ],
)
def test_a_fact_that_decides_the_shape_is_never_invented(
    field: str, label: str
) -> None:
    """`ModelArchitecture` is full of holes by design — the module refuses to invent a
    dimension it could not read. An import that filled them in would produce a
    plausible model of nothing, so the required ones refuse instead."""
    result = importer.from_architecture(_llama_metadata(**{field: None}))
    assert result.graph is None
    assert label in result.missing
    assert label in (result.error or "")


def test_a_missing_head_count_refuses_too() -> None:
    result = importer.from_architecture(_llama_metadata(attention=AttentionSpec()))
    assert result.graph is None
    assert "attention head count" in result.missing


def test_every_choice_made_for_absent_metadata_is_reported() -> None:
    """The facts that only *shape* the graph are assumed rather than refused — but an
    assumption nobody is told about is indistinguishable from a measurement."""
    sparse = ModelArchitecture(
        source="huggingface",
        model="mystery",
        hiddenSize=1024,
        layers=12,
        vocabSize=32000,
        attention=AttentionSpec(heads=16),
    )
    result = importer.from_architecture(sparse)
    assert result.graph is not None
    joined = " ".join(result.assumed).lower()
    for expected in (
        "kv-head",
        "feed-forward width",
        "gated",
        "normalisation",
        "rotary",
    ):
        assert expected in joined, f"{expected!r} was assumed silently"


def test_a_tied_embedding_explains_the_parameter_gap() -> None:
    """On Llama 3.2 3B our count is 12% above the stated one, and the whole difference
    is the output head the model ties to its embedding. Reporting the number without
    the reason reads as a broken import."""
    graph = importer.from_architecture(_llama_metadata()).graph
    assert graph is not None
    estimated = infer(graph).totalParams
    tied = estimated - 128256 * 3072
    result = importer.from_architecture(_llama_metadata(parameterCount=tied))
    assert result.estimatedParams == estimated
    assert any("ties it to the embedding" in note for note in result.notes)


def test_a_gap_we_cannot_explain_says_so_rather_than_going_quiet() -> None:
    result = importer.from_architecture(_llama_metadata(parameterCount=999_999_999))
    assert any("cannot account" in note for note in result.notes)


def test_a_count_that_agrees_adds_no_note() -> None:
    graph = importer.from_architecture(_llama_metadata()).graph
    assert graph is not None
    exact = infer(graph).totalParams
    result = importer.from_architecture(_llama_metadata(parameterCount=exact))
    assert not any("parameters against" in note for note in result.notes)


def test_a_mixture_of_experts_imports_as_a_router_not_a_dense_ffn() -> None:
    result = importer.from_architecture(
        _llama_metadata(
            moe=MoeSpec(experts=8, expertsPerToken=2, expertIntermediateSize=1408)
        )
    )
    assert result.graph is not None
    block = result.graph.groups[0]
    assert any(node.type == "ffn.moe" for node in block.nodes)


# ── agent tools ──────────────────────────────────────────────────────────────────


def test_the_designer_tools_are_grouped_so_they_cost_nothing_when_unused() -> None:
    """An ungrouped namespaced tool joins the always-on core and is charged to every
    turn of every agent. Five of them would be a quiet tax on unrelated work."""
    assert all(tool.group == "model" for tool in agent_tools._TOOLS)
    assert all(tool.name.startswith("model.") for tool in agent_tools._TOOLS)


def test_inspecting_a_design_reports_its_shape_and_its_problems() -> None:
    store.save("Demo", examples.llama_small())
    out = asyncio.run(agent_tools._inspect_design({"name": "Demo"}))
    assert out["className"] == "TinyLlama"
    assert out["estimatedParams"] > 0
    assert out["estimated"] is True
    assert out["groups"][0]["name"] == "DecoderBlock"
    assert out["problems"] == []


def test_a_missing_design_lists_the_ones_that_exist() -> None:
    store.save("Demo", examples.llama_small())
    out = asyncio.run(agent_tools._inspect_design({"name": "Nope"}))
    assert "error" in out
    assert [row["name"] for row in out["designs"]] == ["Demo"]


def test_retuning_a_design_reports_what_it_cost() -> None:
    store.save("Demo", examples.llama_small())
    before = infer(examples.llama_small()).totalParams
    out = asyncio.run(
        agent_tools._set_config({"name": "Demo", "values": {"d_model": 256}})
    )
    assert out["applied"] is True
    assert out["estimatedParamsBefore"] == before
    assert out["estimatedParams"] < before
    assert store.load("Demo").graph.config["d_model"] == 256


def test_a_config_key_no_node_reads_is_refused_rather_than_added() -> None:
    """Adding a key would be read by nothing, so the call would report success and
    change the model not at all — the worst of both outcomes."""
    store.save("Demo", examples.llama_small())
    out = asyncio.run(
        agent_tools._set_config({"name": "Demo", "values": {"widht": 256}})
    )
    assert "error" in out and "widht" in out["error"]


def test_a_config_that_breaks_the_model_is_not_written() -> None:
    """`n_heads = 7` does not divide the KV heads, so the graph stops describing a
    runnable model. Saving would also rewrite the `.py` sitting beside it."""
    store.save("Demo", examples.llama_small())
    out = asyncio.run(
        agent_tools._set_config({"name": "Demo", "values": {"n_heads": 7}})
    )
    assert out["applied"] is False
    assert out["problems"]
    assert store.load("Demo").graph.config["n_heads"] == 8


def test_generated_code_is_capped_and_says_when_it_was_cut() -> None:
    """A truncated result that does not admit it is a result the caller will quote
    back as the whole module — and the missing half is always the end of the file."""
    store.save("Demo", examples.llama_small())
    full = generate(examples.llama_small()).source
    assert len(full) > agent_tools.MAX_SOURCE_CHARS, "fixture is too small to cap"

    out = asyncio.run(agent_tools._generate_code({"name": "Demo"}))
    assert out["lines"] == full.count("\n") + 1
    assert len(out["source"]) == agent_tools.MAX_SOURCE_CHARS
    assert out["truncated"] is True


# ── handing a design to a training project ───────────────────────────────────────


def _project(tmp_path) -> ProjectModel:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    return ProjectModel(id="p1", name="Proj", provider="local", root=str(root))


def _notebook(project: ProjectModel, cells: list) -> None:
    import nbformat

    from backend.notebook_core import notebooks as core

    nb = nbformat.v4.new_notebook()
    nb.cells = cells
    core.save(Path(project.root) / "main.ipynb", nb)


def _cells(project: ProjectModel) -> list:
    from backend.notebook_core import notebooks as core

    return core.load(Path(project.root) / "main.ipynb").cells


def test_the_handoff_writes_a_module_and_a_cell_block(tmp_path) -> None:
    import nbformat

    project = _project(tmp_path)
    _notebook(project, [nbformat.v4.new_code_cell("import torch")])

    result = handoff.apply(examples.llama_small(), project)
    assert result.ok
    assert result.className == "TinyLlama"
    assert (Path(project.root) / "model.py").read_text(encoding="utf-8").startswith('"""')
    sources = [c.source for c in _cells(project)]
    assert any("from model import TinyLlama" in s for s in sources)


def test_the_model_block_lands_above_the_recipe_that_consumes_it(tmp_path) -> None:
    """A notebook where the trainer is configured above the model it trains does not
    run. Appending is only right when there is nothing to be above."""
    import nbformat

    from backend.modules.training import recipes

    project = _project(tmp_path)
    trainer = nbformat.v4.new_markdown_cell(recipes.marker_cell())
    trainer.metadata["horrible_recipe"] = True
    _notebook(project, [nbformat.v4.new_code_cell("import torch"), trainer])

    handoff.apply(examples.llama_small(), project)
    kinds = [
        "model"
        if c.metadata.get(handoff.MARKER_KEY)
        else "recipe"
        if c.metadata.get("horrible_recipe")
        else "other"
        for c in _cells(project)
    ]
    assert kinds.index("model") < kinds.index("recipe")


def test_the_model_block_does_not_use_the_recipe_marker(tmp_path) -> None:
    """`recipes.apply_to_notebook` replaces *everything* carrying `horrible_recipe`.
    Sharing the flag would make a recipe regenerate delete the model definition, and
    a model regenerate delete the trainer config — each silently."""
    import nbformat

    project = _project(tmp_path)
    _notebook(project, [nbformat.v4.new_code_cell("import torch")])
    handoff.apply(examples.llama_small(), project)
    assert handoff.MARKER_KEY != "horrible_recipe"
    for cell in _cells(project):
        if cell.metadata.get(handoff.MARKER_KEY):
            assert not cell.metadata.get("horrible_recipe")


def test_regenerating_replaces_the_block_rather_than_stacking_a_second(tmp_path) -> None:
    import nbformat

    project = _project(tmp_path)
    _notebook(project, [nbformat.v4.new_code_cell("import torch")])
    handoff.apply(examples.llama_small(), project)
    first = len(_cells(project))
    second = handoff.apply(examples.gpt_small(), project)
    assert second.replaced
    assert len(_cells(project)) == first
    sources = [c.source for c in _cells(project)]
    assert any("from model import NanoGPT" in s for s in sources)
    assert not any("TinyLlama" in s for s in sources)


def test_a_project_with_no_notebook_still_gets_the_module(tmp_path) -> None:
    """The module is importable either way. Failing the whole hand-off over a
    notebook that may not be part of this project would be the wrong call."""
    project = _project(tmp_path)
    result = handoff.apply(examples.llama_small(), project)
    assert result.ok
    assert result.cells == 0
    assert "no main.ipynb" in result.message
    assert (Path(project.root) / "model.py").exists()


def test_a_design_that_generates_nothing_is_refused(tmp_path) -> None:
    project = _project(tmp_path)
    broken = examples.llama_small()
    broken.edges = [e for e in broken.edges if e.target != "output"]
    result = handoff.apply(broken, project)
    assert not result.ok
    assert not (Path(project.root) / "model.py").exists()


# ── tracing a real module into a design ──────────────────────────────────────────


def _trace(nodes: list[dict], edges: list[dict]):
    return tracer.build(nodes, edges, "pkg.mod.MyNet")


def test_a_trace_becomes_a_graph_with_its_terminals() -> None:
    graph, mapped, opaque = _trace(
        [
            {"id": "x", "op": "placeholder", "target": "x"},
            {"id": "fc", "op": "call_module", "target": "fc", "cls": "Linear", "shape": [2, 8, 16]},
            {"id": "out", "op": "output", "target": "output"},
        ],
        [{"from": "x", "to": "fc"}, {"from": "fc", "to": "out"}],
    )
    assert [n.type for n in graph.nodes] == ["io.input", "ffn.linear", "io.output"]
    assert graph.nodes[1].params["out_features"] == 16
    assert mapped == 1
    assert opaque == []
    assert len(graph.edges) == 2


def test_an_unmapped_module_raises_rather_than_passing_through() -> None:
    """A stub returning its input unchanged would compile, run, train, and be a
    different model than the one traced — wrong with nothing to report it. So the
    placeholder raises, and the design refuses to run until it is filled in."""
    graph, mapped, opaque = _trace(
        [
            {"id": "x", "op": "placeholder", "target": "x"},
            {"id": "blk", "op": "call_module", "target": "blk", "cls": "FancyBlock"},
            {"id": "out", "op": "output", "target": "output"},
        ],
        [{"from": "x", "to": "blk"}, {"from": "blk", "to": "out"}],
    )
    assert mapped == 0
    assert opaque == ["FancyBlock"]
    stub = graph.nodes[1]
    assert stub.type == "custom.module"
    assert "raise NotImplementedError" in str(stub.params["code"])
    assert "return x" not in str(stub.params["code"])


def test_a_traced_placeholder_still_defines_its_class() -> None:
    """A `custom.module` whose code never reaches the file is a `NameError` at import
    from a node whose whole purpose is to carry code we could not map."""
    graph, _mapped, _opaque = _trace(
        [
            {"id": "x", "op": "placeholder", "target": "x"},
            {"id": "blk", "op": "call_module", "target": "blk", "cls": "FancyBlock"},
            {"id": "out", "op": "output", "target": "output"},
        ],
        [{"from": "x", "to": "blk"}, {"from": "blk", "to": "out"}],
    )
    result = generate(graph)
    assert result.error is None
    assert "class FancyBlock(nn.Module):" in result.source
    ast.parse(result.source)


def test_an_edge_to_a_node_the_trace_dropped_is_dropped_too() -> None:
    graph, _mapped, _opaque = _trace(
        [{"id": "x", "op": "placeholder", "target": "x"}],
        [{"from": "x", "to": "ghost"}, {"from": "nowhere", "to": "x"}],
    )
    assert graph.edges == []


def test_tracing_without_a_project_says_it_could_not_ask() -> None:
    """Three states, never two — the same rule the probe follows. Reporting a model
    as traced when nothing traced it is the failure this module exists to prevent."""
    result = tracer.trace(None, "pkg.mod.Net")
    assert result.status == "unavailable"
    assert result.graph is None
    assert "training project" in result.message
