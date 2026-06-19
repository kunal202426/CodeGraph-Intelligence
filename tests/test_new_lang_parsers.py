"""Smoke tests for the new language parsers (Kotlin, C#, Scala, Bash, Elixir, R, Julia, Haskell, OCaml)."""

from __future__ import annotations

from pathlib import Path

import pytest
from codegraph.parsers.base import IParser
from codegraph.parsers.bash import BashParser
from codegraph.parsers.csharp import CSharpParser
from codegraph.parsers.elixir import ElixirParser
from codegraph.parsers.haskell import HaskellParser
from codegraph.parsers.julia import JuliaParser
from codegraph.parsers.kotlin import KotlinParser
from codegraph.parsers.ocaml import OCamlParser
from codegraph.parsers.r import RParser
from codegraph.parsers.scala import ScalaParser
from codegraph.uir import EntityType, Language

# ------------------------------------------------------------------
# Protocol conformance


@pytest.mark.parametrize(
    "parser,expected_lang",
    [
        (KotlinParser(), Language.KOTLIN),
        (CSharpParser(), Language.CSHARP),
        (ScalaParser(), Language.SCALA),
        (BashParser(), Language.BASH),
        (ElixirParser(), Language.ELIXIR),
        (RParser(), Language.R),
        (JuliaParser(), Language.JULIA),
        (HaskellParser(), Language.HASKELL),
        (OCamlParser(), Language.OCAML),
    ],
)
def test_implements_iparser_protocol(parser, expected_lang) -> None:
    assert isinstance(parser, IParser)
    assert parser.language == expected_lang


@pytest.mark.parametrize(
    "parser,path,empty_src",
    [
        (KotlinParser(), Path("pkg/Main.kt"), ""),
        (CSharpParser(), Path("src/Foo.cs"), ""),
        (ScalaParser(), Path("src/Main.scala"), ""),
        (BashParser(), Path("scripts/deploy.sh"), ""),
        (ElixirParser(), Path("lib/user.ex"), ""),
        (RParser(), Path("analysis/stats.r"), ""),
        (JuliaParser(), Path("src/main.jl"), ""),
        (HaskellParser(), Path("src/Main.hs"), ""),
        (OCamlParser(), Path("lib/lib.ml"), ""),
    ],
)
def test_empty_source_yields_only_module(parser, path, empty_src) -> None:
    result = parser.parse(path, empty_src)
    assert len(result.entities) == 1
    assert result.entities[0].type == EntityType.MODULE
    assert result.edges == []


# ------------------------------------------------------------------
# Kotlin


def test_kotlin_class_and_method() -> None:
    src = "class Foo {\n    fun bar(x: Int): String = x.toString()\n}\nfun topLevel() {}"
    r = KotlinParser().parse(Path("Main.kt"), src)
    names = {e.name for e in r.entities}
    qnames = {e.qualified_name for e in r.entities}
    assert "Foo" in names
    assert "topLevel" in names
    assert "bar" in names
    assert "Foo.bar" in qnames

    cls = next(e for e in r.entities if e.name == "Foo")
    assert cls.type == EntityType.CLASS

    method = next(e for e in r.entities if e.name == "bar")
    assert method.type == EntityType.METHOD
    assert method.parent_id == cls.entity_id


def test_kotlin_interface() -> None:
    src = "interface MyInterface {\n    fun method(): Unit\n}"
    r = KotlinParser().parse(Path("api.kt"), src)
    iface = next((e for e in r.entities if e.name == "MyInterface"), None)
    assert iface is not None
    assert iface.type == EntityType.INTERFACE


def test_kotlin_import_edges() -> None:
    src = "import kotlin.io.File\nimport java.util.*\nfun main() {}"
    r = KotlinParser().parse(Path("main.kt"), src)
    import_edges = [e for e in r.edges if e.type == "imports"]
    dst_ids = {e.dst_id for e in import_edges}
    assert "kt:?:kotlin.io.File" in dst_ids
    assert "kt:?:java.util.*" in dst_ids


def test_kotlin_module_entity_id() -> None:
    r = KotlinParser().parse(Path("pkg/Main.kt"), "")
    mod = r.entities[0]
    assert mod.entity_id == "kt:pkg/Main.kt:pkg.Main"
    assert mod.language == Language.KOTLIN


# ------------------------------------------------------------------
# C#


def test_csharp_class_and_methods() -> None:
    src = "class Foo {\n  public void Bar() {}\n  public Foo() {}\n}"
    r = CSharpParser().parse(Path("Foo.cs"), src)
    qnames = {e.qualified_name for e in r.entities}
    assert "Foo" in qnames
    assert "Foo.Bar" in qnames
    assert "Foo.Foo" in qnames

    method = next(e for e in r.entities if e.qualified_name == "Foo.Bar")
    assert method.type == EntityType.METHOD


def test_csharp_interface() -> None:
    src = "interface IFoo {\n  void Bar();\n}"
    r = CSharpParser().parse(Path("IFoo.cs"), src)
    iface = next(e for e in r.entities if e.name == "IFoo" and e.type == EntityType.INTERFACE)
    assert iface is not None


def test_csharp_namespace_traversal() -> None:
    src = "namespace MyApp {\n  class Service {\n    public void Run() {}\n  }\n}"
    r = CSharpParser().parse(Path("Service.cs"), src)
    qnames = {e.qualified_name for e in r.entities}
    assert "Service" in qnames
    assert "Service.Run" in qnames


def test_csharp_module_entity_id() -> None:
    r = CSharpParser().parse(Path("src/Foo.cs"), "")
    mod = r.entities[0]
    assert mod.entity_id == "cs:src/Foo.cs:src.Foo"
    assert mod.language == Language.CSHARP


# ------------------------------------------------------------------
# Scala


def test_scala_class_trait_object() -> None:
    src = "class Foo {\n  def bar() = 1\n}\ntrait T {\n  def m(): Unit\n}\nobject O { def f() = 1 }"
    r = ScalaParser().parse(Path("Main.scala"), src)
    names = {e.name for e in r.entities}
    qnames = {e.qualified_name for e in r.entities}
    assert "Foo" in names
    assert "T" in names
    assert "O" in names
    assert "Foo.bar" in qnames
    assert "T.m" in qnames
    assert "O.f" in qnames

    cls = next(e for e in r.entities if e.name == "Foo")
    assert cls.type == EntityType.CLASS

    trait = next(e for e in r.entities if e.name == "T")
    assert trait.type == EntityType.INTERFACE


def test_scala_import_edge() -> None:
    src = "import scala.io.Source\nclass Foo {}"
    r = ScalaParser().parse(Path("Main.scala"), src)
    imports = [e for e in r.edges if e.type == "imports"]
    assert any("scala.io.Source" in e.dst_id for e in imports)


def test_scala_module_entity_id() -> None:
    r = ScalaParser().parse(Path("src/Main.scala"), "")
    mod = r.entities[0]
    assert mod.entity_id == "scala:src/Main.scala:src.Main"
    assert mod.language == Language.SCALA


# ------------------------------------------------------------------
# Bash


def test_bash_function_keyword_style() -> None:
    src = "function greet() {\n  echo hi\n}"
    r = BashParser().parse(Path("deploy.sh"), src)
    fn = next((e for e in r.entities if e.name == "greet"), None)
    assert fn is not None
    assert fn.type == EntityType.FUNCTION


def test_bash_posix_style_function() -> None:
    src = "install() {\n  apt-get install -y git\n}"
    r = BashParser().parse(Path("setup.sh"), src)
    fn = next((e for e in r.entities if e.name == "install"), None)
    assert fn is not None
    assert fn.type == EntityType.FUNCTION


def test_bash_module_entity_id() -> None:
    r = BashParser().parse(Path("scripts/deploy.sh"), "")
    mod = r.entities[0]
    assert mod.entity_id == "sh:scripts/deploy.sh:scripts.deploy"
    assert mod.language == Language.BASH


# ------------------------------------------------------------------
# Elixir


def test_elixir_defmodule_and_def() -> None:
    src = "defmodule MyApp.User do\n  def greet(name) do\n    name\n  end\n  defp private_fn(x), do: x\nend"
    r = ElixirParser().parse(Path("lib/user.ex"), src)
    names = {e.name for e in r.entities}
    assert "MyApp.User" in names
    assert "greet" in names
    assert "private_fn" in names

    mod = next(e for e in r.entities if e.name == "MyApp.User")
    assert mod.type == EntityType.MODULE

    pub_fn = next(e for e in r.entities if e.name == "greet")
    assert pub_fn.is_exported is True

    priv_fn = next(e for e in r.entities if e.name == "private_fn")
    assert priv_fn.is_exported is False


def test_elixir_module_entity_id() -> None:
    r = ElixirParser().parse(Path("lib/user.ex"), "")
    mod = r.entities[0]
    assert mod.entity_id == "ex:lib/user.ex:lib.user"
    assert mod.language == Language.ELIXIR


# ------------------------------------------------------------------
# R


def test_r_arrow_assignment_function() -> None:
    src = "my_func <- function(x, y) {\n  x + y\n}"
    r = RParser().parse(Path("analysis.r"), src)
    fn = next((e for e in r.entities if e.name == "my_func"), None)
    assert fn is not None
    assert fn.type == EntityType.FUNCTION


def test_r_equals_assignment_function() -> None:
    src = "helper = function(z) z * 2"
    r = RParser().parse(Path("utils.r"), src)
    fn = next((e for e in r.entities if e.name == "helper"), None)
    assert fn is not None
    assert fn.type == EntityType.FUNCTION


def test_r_module_entity_id() -> None:
    r = RParser().parse(Path("analysis/stats.r"), "")
    mod = r.entities[0]
    assert mod.entity_id == "r:analysis/stats.r:analysis.stats"
    assert mod.language == Language.R


# ------------------------------------------------------------------
# Julia


def test_julia_function_and_struct() -> None:
    src = "function greet(name::String)\n  println(name)\nend\nstruct Point\n  x::Float64\nend\nabstract type Shape end"
    r = JuliaParser().parse(Path("main.jl"), src)
    names = {e.name for e in r.entities}
    assert "greet" in names
    assert "Point" in names
    assert "Shape" in names

    fn = next(e for e in r.entities if e.name == "greet")
    assert fn.type == EntityType.FUNCTION

    st = next(e for e in r.entities if e.name == "Point")
    assert st.type == EntityType.CLASS

    ab = next(e for e in r.entities if e.name == "Shape")
    assert ab.type == EntityType.INTERFACE


def test_julia_module_entity_id() -> None:
    r = JuliaParser().parse(Path("src/main.jl"), "")
    mod = r.entities[0]
    assert mod.entity_id == "jl:src/main.jl:src.main"
    assert mod.language == Language.JULIA


# ------------------------------------------------------------------
# Haskell


def test_haskell_function_adt_class() -> None:
    src = 'data Tree a = Leaf | Node a\nclass Container f where\n  empty :: f a\ngreet name = "Hello"'
    r = HaskellParser().parse(Path("Main.hs"), src)
    names = {e.name for e in r.entities}
    assert "Tree" in names
    assert "Container" in names
    assert "greet" in names

    tree = next(e for e in r.entities if e.name == "Tree")
    assert tree.type == EntityType.CLASS

    container = next(e for e in r.entities if e.name == "Container")
    assert container.type == EntityType.INTERFACE

    fn = next(e for e in r.entities if e.name == "greet")
    assert fn.type == EntityType.FUNCTION


def test_haskell_deduplicates_multiple_clauses() -> None:
    src = "f 0 = 0\nf n = n + 1"
    r = HaskellParser().parse(Path("Main.hs"), src)
    fns = [e for e in r.entities if e.name == "f"]
    assert len(fns) == 1


def test_haskell_module_entity_id() -> None:
    r = HaskellParser().parse(Path("src/Main.hs"), "")
    mod = r.entities[0]
    assert mod.entity_id == "hs:src/Main.hs:src.Main"
    assert mod.language == Language.HASKELL


# ------------------------------------------------------------------
# OCaml


def test_ocaml_value_definition() -> None:
    src = "let add x y = x + y\nlet greet name = name"
    r = OCamlParser().parse(Path("lib.ml"), src)
    names = {e.name for e in r.entities}
    assert "add" in names
    assert "greet" in names

    fn = next(e for e in r.entities if e.name == "add")
    assert fn.type == EntityType.FUNCTION


def test_ocaml_class_definition() -> None:
    src = "class animal name =\n  object\n    method speak () = name\n  end"
    r = OCamlParser().parse(Path("lib.ml"), src)
    cls = next((e for e in r.entities if e.name == "animal"), None)
    assert cls is not None
    assert cls.type == EntityType.CLASS


def test_ocaml_module_definition() -> None:
    src = "module MyLib = struct\n  let f x = x\nend"
    r = OCamlParser().parse(Path("main.ml"), src)
    mod = next((e for e in r.entities if e.name == "MyLib"), None)
    assert mod is not None
    assert mod.type == EntityType.MODULE


def test_ocaml_module_entity_id() -> None:
    r = OCamlParser().parse(Path("lib/main.ml"), "")
    file_mod = r.entities[0]
    assert file_mod.entity_id == "ml:lib/main.ml:lib.main"
    assert file_mod.language == Language.OCAML
