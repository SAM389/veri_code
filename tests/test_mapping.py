from pathlib import Path

import pytest

from vericode.extract import ImportRef
from vericode.mapping import Resolver, import_map


def _ref(module: str, *names: str, file: str = "m.py") -> ImportRef:
    return ImportRef(module, file, 1, names=names)


def _pkgs(resolver: Resolver, ref: ImportRef) -> list[tuple[str, str]]:
    return [(r.package, r.source) for r in resolver.resolve(ref)]


@pytest.fixture
def resolver(tmp_path: Path) -> Resolver:
    return Resolver(tmp_path)


@pytest.mark.parametrize(
    ("ref", "package"),
    [
        (_ref("cv2"), "opencv-python"),
        (_ref("yaml"), "pyyaml"),
        (_ref("PIL", "Image"), "pillow"),
        (_ref("sklearn.linear_model", "LogisticRegression"), "scikit-learn"),
        (_ref("bs4", "BeautifulSoup"), "beautifulsoup4"),
        (_ref("dateutil.parser"), "python-dateutil"),
        (_ref("google.protobuf", "json_format"), "protobuf"),
        (_ref("google.cloud", "storage"), "google-cloud-storage"),
        (_ref("google.cloud.storage"), "google-cloud-storage"),
        (_ref("azure.storage.blob", "BlobClient"), "azure-storage-blob"),
        (_ref("mysql.connector"), "mysql-connector-python"),
    ],
)
def test_known_mismatches_map(resolver: Resolver, ref: ImportRef, package: str) -> None:
    assert _pkgs(resolver, ref) == [(package, "map")]


def test_map_keys_are_case_sensitive_and_values_normalized() -> None:
    m = import_map()
    assert m["PIL"] == "pillow"
    assert "pil" not in m
    assert all(v == v.lower() and "_" not in v and "." not in v for v in m.values())


def test_plain_import_falls_back_to_its_own_name(resolver: Resolver) -> None:
    assert _pkgs(resolver, _ref("requests")) == [("requests", "import-name")]
    assert _pkgs(resolver, _ref("typing_extensions")) == [("typing-extensions", "import-name")]


def test_from_import_names_dedupe_to_one_package(resolver: Resolver) -> None:
    assert _pkgs(resolver, _ref("requests", "get", "post", "Session")) == [
        ("requests", "import-name")
    ]


def test_from_namespace_import_resolves_each_name(resolver: Resolver) -> None:
    assert _pkgs(resolver, _ref("google.cloud", "storage", "bigquery")) == [
        ("google-cloud-storage", "map"),
        ("google-cloud-bigquery", "map"),
    ]


def test_unmapped_namespace_subpackage_is_a_guess(resolver: Resolver) -> None:
    [r] = resolver.resolve(_ref("azure.mgmt.compute", "ComputeManagementClient"))
    assert (r.package, r.source, r.guessed) == ("azure-mgmt-compute", "namespace", True)
    [r] = resolver.resolve(_ref("google.cloud.some_service_v1"))
    assert r.package == "google-cloud-some-service-v1"


def test_bare_namespace_root_is_not_looked_up(resolver: Resolver) -> None:
    assert resolver.resolve(_ref("google")) == []
    assert resolver.resolve(_ref("google.cloud")) == []
    assert resolver.resolve(_ref("azure")) == []


def test_installed_metadata_wins(tmp_path: Path) -> None:
    r = Resolver(tmp_path, installed={"cv2": ["opencv-python-headless"]})
    assert _pkgs(r, _ref("cv2")) == [("opencv-python-headless", "installed")]


def test_ambiguous_installed_prefers_declared_else_falls_through(tmp_path: Path) -> None:
    installed = {"jwt": ["PyJWT", "python-jwt"]}
    r = Resolver(tmp_path, installed=installed, declared=["python-jwt"])
    assert _pkgs(r, _ref("jwt")) == [("python-jwt", "installed")]
    r = Resolver(tmp_path, installed=installed)
    assert _pkgs(r, _ref("jwt")) == [("pyjwt", "map")]


def test_installed_metadata_ignored_for_namespaces(tmp_path: Path) -> None:
    r = Resolver(tmp_path, installed={"google": ["protobuf"]})
    assert _pkgs(r, _ref("google.cloud", "storage")) == [("google-cloud-storage", "map")]


@pytest.mark.parametrize(
    ("module", "declared"),
    [
        ("magic_lib", "python-magic-lib"),
        ("foo", "pyfoo"),
        ("yaml", "PyYAML"),
        ("thing", "thing-python"),
    ],
)
def test_declared_dependency_variants(tmp_path: Path, module: str, declared: str) -> None:
    r = Resolver(tmp_path, declared=[declared])
    [res] = r.resolve(_ref(module))
    assert res.source == "declared"
    assert res.package == declared.lower()


def test_declared_namespace_package(tmp_path: Path) -> None:
    r = Resolver(tmp_path, declared=["azure-mgmt-compute"])
    assert _pkgs(r, _ref("azure.mgmt.compute")) == [("azure-mgmt-compute", "declared")]


def test_stdlib_skipped(resolver: Resolver) -> None:
    assert resolver.resolve(_ref("os.path")) == []
    assert resolver.resolve(_ref("__future__", "annotations")) == []


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")


def test_local_modules_are_never_looked_up(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "utils/__init__.py",  # package
        "helpers.py",  # module
        "nsdir/mod.py",  # namespace package (no __init__)
        "src/mypkg/__init__.py",  # src layout
        "scripts/tool.py",
        "scripts/sibling.py",  # sibling of the importing script
        "app/sub/deep.py",
        "app/shared_mod.py",  # ancestor dir of the importing file
        "data/readme.txt",  # dir without python: not a module
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "My-Project"\n', "utf-8")
    r = Resolver(tmp_path)
    for module in ("utils", "helpers", "nsdir.mod", "mypkg", "my_project"):
        assert r.resolve(_ref(module)) == [], module
    assert r.resolve(_ref("sibling", file="scripts/tool.py")) == []
    assert r.resolve(_ref("shared_mod", file="app/sub/deep.py")) == []
    assert _pkgs(r, _ref("data")) == [("data", "import-name")]
    assert _pkgs(r, _ref("sibling")) == [("sibling", "import-name")]  # not a sibling of m.py


def test_venv_and_hidden_dirs_are_not_local(tmp_path: Path) -> None:
    _touch(tmp_path, ".venv/pyvenv.cfg", ".venv/x.py", "venv2/pyvenv.cfg", "venv2/y.py")
    r = Resolver(tmp_path)
    assert r.resolve(_ref("venv2")) != []


def test_allowlisted_names_are_never_looked_up(tmp_path: Path) -> None:
    r = Resolver(tmp_path, allow=["acme-internal", "opencv-python"])
    assert r.resolve(_ref("acme_internal.client")) == []
    assert r.resolve(_ref("cv2")) == []  # allowlisted by resolved package name
    assert _pkgs(r, _ref("requests")) == [("requests", "import-name")]
