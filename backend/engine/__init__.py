"""
Universal AI Office Template Understanding & Generation Engine.

    AI understands.  Rules validate.  Renderer modifies.  QA verifies.

Quick start:

    from engine import TemplateEngine
    result = TemplateEngine().fill("MOM_Template.xlsx", "summary.md")
    print(result.summary())
    print(result.audit.render_text())
"""

from .audit import AuditLog, build_audit  # noqa: F401
from .ir import DocType, Node, NodeType, TableIR, TemplateIR, ValueFormat  # noqa: F401
from .mapper import Decision, FieldMapper, FillPlan, Policy, build_plan  # noqa: F401
from .parsers import parse_template  # noqa: F401
from .pipeline import EngineConfig, FillResult, TemplateEngine  # noqa: F401
from .qa import structural_qa, visual_qa  # noqa: F401
from .renderers import render_plan  # noqa: F401
from .semantic.llm import get_llm  # noqa: F401
from .semantic.roles import DEFAULT_REGISTRY, RoleDef, RoleRegistry  # noqa: F401
from .source import CanonicalSource, extract_source  # noqa: F401
from .spec import SpecBuilder, TemplateSpec, build_spec  # noqa: F401
from .templatize import TemplatizeResult, templatize  # noqa: F401
from .validation import ValidationGate, ValidationReport, validate  # noqa: F401

__version__ = "1.0.0"
__all__ = ["TemplateEngine", "EngineConfig", "FillResult", "Policy",
           "parse_template", "build_spec", "TemplateSpec", "extract_source",
           "CanonicalSource", "build_plan", "FillPlan", "validate",
           "render_plan", "structural_qa", "visual_qa", "build_audit",
           "RoleRegistry", "RoleDef", "get_llm", "templatize", "TemplatizeResult"]
