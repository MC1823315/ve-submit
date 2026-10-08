"""Fixed scientific identities, usable by the client without the harness."""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ScientificTrack:
    name: str
    board_key: str
    solution_root: str
    input_relative: str


SCIENTIFIC_TRACKS = MappingProxyType({
    row[0]: ScientificTrack(*row) for row in (
        ("t1-temporal-val", "T1:val", "solution-t1", "T1/E9.5_RNA.h5ad"),
        ("t2-heart-interp", "T2:heart:val_interp", "solution", "T2/E8.25_late.h5ad"),
        ("t2-heart-extrap", "T2:heart:val_extrap", "solution-t2-heart-extrap", "T2/E9.5.h5ad"),
        ("t2-embryo-interp", "T2:embryo:val_interp", "solution-t2-embryo-interp", "T2/E7.25.h5ad"),
        ("t3-gata4", "T3:gata4", "solution-t3", "T2/E8.75.h5ad"),
    )
})


def scientific_track(name):
    if type(name) is not str or name not in SCIENTIFIC_TRACKS:
        raise ValueError("installed scientific track required")
    return SCIENTIFIC_TRACKS[name]


def require_solution_root(root):
    if type(root) is not str or root not in {t.solution_root for t in SCIENTIFIC_TRACKS.values()}:
        raise ValueError("allowlisted scientific source root required")
    return root
