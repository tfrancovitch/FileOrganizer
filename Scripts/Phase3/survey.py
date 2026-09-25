r"""Survey Mode (Phase 3, Build 7): a small cluster of look-alikes at once.

A pure function of the model, like `bulk.py`, whose pipeline it feeds.

What a cluster is. Two images are in the same cluster when a chain of
candidate pairs joins them -- inside the Show list a person is looking at,
counting only the pairs still open (not Resolved, not Ready for Plan, not
byte-identical). That is a connected component of the candidate graph, and
it is a DISPLAY convenience, never a semantic group (P3.R3 §6, §34): A~B
and B~C put A, B and C on one screen, and say nothing about A~C. No cluster
is ever recorded, and nothing here writes a decision about one.

What a cluster's pairs are. Every current candidate pair between two of the
images shown -- including one outside the Show list, or already decided --
because those are the pairs a person looking at these images can see. A
pair of images with no candidate pair between them (A and C above) is a
non-edge: nothing is ever recorded across it.

Survey Mode is for 3-8 images (R3 §14). A cluster of two images is an
isolated pair -- Compare Mode already shows it. A cluster larger than eight
is reported, with its size and folder spread, and left reviewable exactly
as it is today (pair by pair, or a folder batch): no paging, no ranking,
nothing Tournament-shaped (R3 §15).

The group controls are Build 6 batches whose population is the pairs of
one displayed cluster (synthesis §3) -- the same preview, exceptions,
conflicts (chain guard included), blocked pairs, commit and undo:

    Keep All                        pairs_keep_both over every pair
    Mark Remaining Equivalent       pairs_equivalent_keep_both over every pair (no Canonical), or
                                    pairs_equivalent_prefer (a Canonical chosen): the pairs that
                                    include the Canonical prefer it unless the other image is a
                                    Keeper; the rest keep both. A pair already answered is preserved.
    Mark Selected as Related        pairs_related_keep_both over the pairs with EXACTLY ONE image
      Variant                       selected -- the pairs connecting the selection to the rest of
                                    the cluster; a pair between two selected images is not touched
    Defer Group                     pairs_defer over every pair no relationship decision has settled

Exclude Candidate takes an image out of the view, and so out of every group
control's population. It records nothing: removing an image from the screen
is not a judgment about the pairs it was in (synthesis §4).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import bulk as BK
from . import routing as RT
from .registry import SCOPE_EXPLICIT_SELECTION

SURVEY_MIN = 3
SURVEY_MAX = 8

#: The routes whose pairs still join images into a cluster. A Resolved or
#: Ready-for-Plan pair is answered; a byte-identical pair belongs to the
#: exact-duplicate review.
OPEN_ROUTES = (RT.UNRESOLVED, RT.CONFLICT, RT.NEEDS_REVALIDATION, RT.BLOCKED, RT.DEFERRED)

ISOLATED = "isolated_pair"
SURVEY = "survey"
OVERSIZED = "oversized"

KEEP_ALL = "keep_all"
REMAINING_EQUIVALENT = "remaining_equivalent"
SELECTED_RELATED = "selected_related"
DEFER_GROUP = "defer_group"
CONTROLS = {
    KEEP_ALL: "Keep All",
    REMAINING_EQUIVALENT: "Mark Remaining Equivalent",
    SELECTED_RELATED: "Mark Selected as Related Variant",
    DEFER_GROUP: "Defer Group",
}


def _id_order(x):
    return (0, int(x), "") if str(x).isdigit() else (1, 0, str(x))


@dataclass
class Cluster:
    """One connected component of the open pairs in a Show list."""
    number: int                  # 1-based, in the Show list's order
    images: tuple                # file_path_ids, in display order
    pair_ids: tuple              # the open pairs of the Show list that joined them
    folders: int = 0

    @property
    def size(self):
        return len(self.images)

    @property
    def kind(self):
        if self.size <= 2:
            return ISOLATED
        return SURVEY if self.size <= SURVEY_MAX else OVERSIZED


@dataclass
class ClusterMap:
    show: str
    clusters: list
    of_pair: dict = field(default_factory=dict)      # pair_id -> Cluster
    of_image: dict = field(default_factory=dict)     # file_path_id -> Cluster

    def of_kind(self, kind):
        return [c for c in self.clusters if c.kind == kind]

    @property
    def counts(self) -> dict:
        out = {ISOLATED: 0, SURVEY: 0, OVERSIZED: 0}
        for c in self.clusters:
            out[c.kind] += 1
        return out

    def summary(self) -> str:
        """The line a person sees: how much of this list Survey Mode reaches."""
        c = self.counts
        survey = self.of_kind(SURVEY)
        big = self.of_kind(OVERSIZED)
        parts = [f"{c[SURVEY]:,} cluster{'s' if c[SURVEY] != 1 else ''} of {SURVEY_MIN}-{SURVEY_MAX} images "
                 f"({sum(len(x.pair_ids) for x in survey):,} pairs)",
                 f"{c[ISOLATED]:,} isolated pair{'s' if c[ISOLATED] != 1 else ''}"]
        if big:
            parts.append(f"{len(big):,} larger cluster{'s' if len(big) != 1 else ''} ({sum(len(x.pair_ids) for x in big):,} pairs; "
                         f"the largest {max(x.size for x in big):,} images) -- reviewed pair by pair or by a folder batch")
        return f"Survey Mode in “{self.show}”: " + "; ".join(parts) + "."


def open_pairs(proj, routing, ev, show):
    """The pairs of a Show list that still join images into clusters."""
    out = []
    for p in BK.filter_pairs(proj, routing, ev, show):
        if p.exact_content:
            continue
        r = routing.pairs.get(p.pair_id)
        if r is not None and r.primary in OPEN_ROUTES:
            out.append(p)
    return out


def cluster_map(proj, routing, ev, show) -> ClusterMap:
    """Connected components of the Show list's open pairs, numbered in the
    list's order (a cluster's place is its first pair's)."""
    pairs = open_pairs(proj, routing, ev, show)
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for p in pairs:
        ra, rb = find(p.a_id), find(p.b_id)
        if ra != rb:
            parent[ra] = rb
    order, members, edges = [], {}, {}
    for p in pairs:
        root = find(p.a_id)
        if root not in members:
            order.append(root)
            members[root] = set()
            edges[root] = []
        members[root].update(p.members)
        edges[root].append(p.pair_id)

    def display(lid):
        loc = ev.locations.get(str(lid))
        return (loc.sort_key if loc is not None else (), _id_order(lid))
    out = ClusterMap(show, [])
    for n, root in enumerate(order, 1):
        images = tuple(sorted(members[root], key=display))
        folders = {BK.folder_of(ev.locations[i].path).lower() for i in images if i in ev.locations}
        c = Cluster(n, images, tuple(edges[root]), len(folders))
        out.clusters.append(c)
        for pid in c.pair_ids:
            out.of_pair[pid] = c
        for lid in images:
            out.of_image[lid] = c
    return out


def adjacency(proj) -> dict:
    """file_path_id -> [pair projections it is in] (current, non-exact)."""
    adj = {}
    for p in proj.pairs:
        if p.exact_content:
            continue
        adj.setdefault(p.a_id, []).append(p)
        adj.setdefault(p.b_id, []).append(p)
    return adj


def cluster_edges(proj, images, adj=None) -> list:
    """Every candidate pair between two of these images, in the pairs' own
    order. A pair of images with no candidate pair is not here, and nothing
    is ever recorded between them."""
    adj = adj if adj is not None else adjacency(proj)
    want = {str(i) for i in images}
    seen, out = set(), []
    for lid in want:
        for p in adj.get(lid, ()):
            if p.pair_id not in seen and p.a_id in want and p.b_id in want:
                seen.add(p.pair_id)
                out.append(p)
    rank = {p.pair_id: i for i, p in enumerate(proj.pairs)}
    out.sort(key=lambda p: rank.get(p.pair_id, 0))
    return out


def non_edges(images, edges, proj=None) -> list:
    """The image pairs shown together with no candidate pair between them.
    With the projection, a byte-identical pair counts as a candidate pair (it
    is one -- the exact-duplicate review answers it), so it is not listed."""
    joined = {frozenset(p.members) for p in edges}
    if proj is not None:
        want = {str(i) for i in images}
        joined |= {frozenset(p.members) for p in proj.pairs if p.exact_content and p.a_id in want and p.b_id in want}
    imgs = list(images)
    return [(a, b) for i, a in enumerate(imgs) for b in imgs[i + 1:] if frozenset((a, b)) not in joined]


def group_action(control, images, edges, keepers=(), canonical=None, selected=(), disposition=None, until=None):
    """What one group control is, as a Build 6 batch: (action key, the pair
    ids it covers, the action's parameters). Raises ValueError with the
    reason when the control cannot apply as asked."""
    if control not in CONTROLS:
        raise ValueError(f"unknown Survey Mode control: {control!r}")
    images = [str(i) for i in images]
    shown = set(images)
    keepers = {str(k) for k in keepers if str(k) in shown}
    selected = {str(s) for s in selected if str(s) in shown}
    canonical = str(canonical) if canonical is not None and str(canonical) in shown else None
    if len(images) < 2 or not edges:
        raise ValueError("no candidate pair is left among the images in view")
    ids = [p.pair_id for p in edges]
    if control == KEEP_ALL:
        return "pairs_keep_both", ids, {}
    if control == REMAINING_EQUIVALENT:
        if canonical is None:
            return "pairs_equivalent_keep_both", ids, {}
        return "pairs_equivalent_prefer", ids, {"prefer": canonical, "keepers": sorted(keepers | {canonical}, key=_id_order)}
    if control == SELECTED_RELATED:
        if not selected:
            raise ValueError("select the image(s) that are variants of the rest first")
        if selected == shown:
            raise ValueError("every image is selected: nothing is left for the selection to be a variant of")
        reach = [p.pair_id for p in edges if (p.a_id in selected) != (p.b_id in selected)]
        if not reach:
            raise ValueError("no candidate pair joins the selected image(s) to the rest of the cluster")
        return "pairs_related_keep_both", reach, {"selected": sorted(selected, key=_id_order)}
    return "pairs_defer", ids, {"disposition": disposition or "defer_indefinitely", "until": until}


def preview_group(ev, proj, routing, control, images, show, keepers=(), canonical=None, selected=(),
                  excluded=(), disposition=None, until=None, conn=None, adj=None):
    """The categorized preview of one group control over the images in view:
    `bulk.preview_pairs` over the cluster's candidate pairs, as an explicit
    selection, with a note of the cluster it was over. Commit it with
    `store.commit_bulk`; undo it with `store.undo_batch`."""
    edges = cluster_edges(proj, images, adj)
    action, ids, params = group_action(control, images, edges, keepers, canonical, selected, disposition, until)
    params = dict(params)
    params["survey"] = {
        "control": CONTROLS[control], "show": show,
        "images": [str(i) for i in images], "edges": [p.pair_id for p in edges],
        "excluded": sorted((str(x) for x in excluded), key=_id_order),
        "keepers": sorted((str(k) for k in keepers), key=_id_order), "canonical": str(canonical) if canonical else None,
        "selected": sorted((str(s) for s in selected), key=_id_order),
    }
    return BK.preview_pairs(ev, proj, routing, action, SCOPE_EXPLICIT_SELECTION, selection=ids, conn=conn, params=params)


def oversized_text(cluster: Cluster) -> str:
    return (f"This pair's cluster holds {cluster.size:,} images and {len(cluster.pair_ids):,} open pairs across "
            f"{cluster.folders:,} folder{'s' if cluster.folders != 1 else ''} -- more than Survey Mode shows "
            f"({SURVEY_MIN}-{SURVEY_MAX} images). It stays reviewable pair by pair here, or with Bulk action... "
            "where its pairs share a folder.")
