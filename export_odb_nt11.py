# -*- coding: utf-8 -*-
# Run with:  abaqus python export_odb_nt11.py --odb "path\to\file.odb" --out export_dir [--steps Thermal_Step,OtherStep]
#
# Outputs in export_dir:
#   mesh.json       : mesh per instance (nodes, elements, element type)
#   steps.json      : steps and frames (time, description)
#   nt11.jsonl      : one JSON object per bucket of NT11 values
#
# Each line in nt11.jsonl has:
# {
#   "step": "Thermal_Step",
#   "frame_index": 3,
#   "frame_value": 12.345,
#   "instance": "PART-1-1",
#   "position": "NODAL" | "ELEMENT_NODAL" | "INTEGRATION_POINT" | "WHOLE_ELEMENT",
#   "section_point": {"number":5,"description":"Top"} or null,
#   "labels": [ ... nodeLabels or elementLabels ... ],
#   "values": [ [v], [v], ... ]   # scalar NT11 wrapped as 1-tuple per Abaqus API
# }

from odbAccess import openOdb
from abaqusConstants import *
import os, sys, json

# ----------------- simple CLI -----------------
def parse_args(argv):
    odb_path = None
    out_dir  = None
    step_filter = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--odb", "-i"):
            i += 1; odb_path = argv[i]
        elif a in ("--out", "-o"):
            i += 1; out_dir = argv[i]
        elif a in ("--steps", "-s"):
            i += 1; step_filter = [s.strip() for s in argv[i].split(",") if s.strip()]
        i += 1
    if not odb_path or not out_dir:
        sys.stderr.write("Usage: abaqus python export_odb_nt11.py --odb path\\to.odb --out export_dir [--steps StepA,StepB]\n")
        sys.exit(2)
    return odb_path, out_dir, step_filter
# ---------------------------------------------

def ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)

def position_name(pos):
    # Map enum to stable string for cross-version import
    if   pos == NODAL:             return "NODAL"
    elif pos == ELEMENT_NODAL:     return "ELEMENT_NODAL"
    elif pos == INTEGRATION_POINT: return "INTEGRATION_POINT"
    elif pos == WHOLE_ELEMENT:     return "WHOLE_ELEMENT"
    # Fallback (rare)
    return str(pos)

def export_mesh(odb, out_dir):
    """Write mesh.json with instances -> nodes/elements, using pure Python types."""
    out = {"instances": {}}
    ra = odb.rootAssembly
    for iname, inst in ra.instances.items():
        inst_rec = {"nodes": [], "elements": []}

        # --- Nodes ---
        for n in inst.nodes:
            coords = tuple(float(x) for x in n.coordinates)
            # normalize to 3 components
            if len(coords) == 2:
                coords = (coords[0], coords[1], 0.0)
            inst_rec["nodes"].append([
                int(n.label),
                float(coords[0]), float(coords[1]), float(coords[2])
            ])

        # --- Elements ---
        if inst.elements:
            for e in inst.elements:
                conn = [int(x) for x in e.connectivity]
                inst_rec["elements"].append({
                    "label": int(e.label),
                    "type":  str(e.type),
                    "connectivity": conn
                })

        out["instances"][iname] = inst_rec

    # Ensure standard floats/ints only before writing
    def convert_to_native(o):
        if isinstance(o, (float, int)):
            return o
        if isinstance(o, (list, tuple)):
            return [convert_to_native(x) for x in o]
        if isinstance(o, dict):
            return {k: convert_to_native(v) for k, v in o.items()}
        return str(o)

    out = convert_to_native(out)

    mesh_path = os.path.join(out_dir, "mesh.json")
    with open(mesh_path, "w") as f:
        json.dump(out, f, indent=2)
    print("✅ Wrote", mesh_path)

def export_steps(odb, step_filter, out_dir):
    """Write steps.json with frame meta (time & description)."""
    steps_out = []
    for sname, step in odb.steps.items():
        if step_filter and sname not in step_filter:
            continue
        frames = []
        for idx in range(len(step.frames)):
            fr = step.frames[idx]
            frames.append({
                "index": idx,
                "value": float(fr.frameValue),
                "description": fr.description if hasattr(fr, "description") else ""
            })
        steps_out.append({
            "name": sname,
            "domain": "TIME" if step.domain == TIME else str(step.domain),
            "numFrames": len(step.frames),
            "frames": frames
        })
    with open(os.path.join(out_dir, "steps.json"), "w") as f:
        json.dump({"steps": steps_out}, f, indent=2)

def sp_to_dict(sp):
    if sp is None:
        return None
    # SectionPoint has .number and .description
    num = getattr(sp, "number", None)
    desc = getattr(sp, "description", None)
    return {"number": int(num) if num is not None else None,
            "description": desc}

def export_nt11(odb, step_filter, out_dir):
    """Write nt11.jsonl. One JSON object per (step, frame, instance, position, sectionPoint)."""
    path = os.path.join(out_dir, "nt11.jsonl")
    out = open(path, "w")

    for sname, step in odb.steps.items():
        if step_filter and sname not in step_filter:
            continue

        for fidx in range(len(step.frames)):
            fr = step.frames[fidx]
            fos = fr.fieldOutputs
            if "NT11" not in fos:
                continue
            fo = fos["NT11"]
            if not fo.values:
                continue

            # bucket values by (instance, position, sectionPoint)
            buckets = {}
            for v in fo.values:
                iname = v.instance.name
                pos   = position_name(v.position)
                sp    = sp_to_dict(getattr(v, "sectionPoint", None))
                key   = (iname, pos, json.dumps(sp, sort_keys=True))  # JSON-encoded SP as key
                b = buckets.setdefault(key, {"labels": [], "values": [], "instance": iname, "position": pos, "section_point": sp})
                # label = nodeLabel for nodal, else elementLabel
                lbl = getattr(v, "nodeLabel", getattr(v, "elementLabel", None))
                # NT11 is scalar -> store as [value] for Abaqus addData semantics
                val = v.data
                try:
                    # ensure float
                    valf = float(val)
                except:
                    # sometimes v.data could be a tuple of length 1
                    if isinstance(val, (list, tuple)) and len(val) == 1:
                        valf = float(val[0])
                    else:
                        raise
                b["labels"].append(int(lbl))
                b["values"].append([valf])

            # write one line per bucket
            for key, b in buckets.items():
                rec = {
                    "step": sname,
                    "frame_index": fidx,
                    "frame_value": float(fr.frameValue),
                    "instance": b["instance"],
                    "position": b["position"],
                    "section_point": b["section_point"],  # or null
                    "labels": b["labels"],
                    "values": b["values"]
                }
                out.write(json.dumps(rec) + "\n")

    out.close()

def main():
    odb_path, out_dir, step_filter = parse_args(sys.argv[1:])
    ensure_dir(out_dir)
    print("Opening ODB:", odb_path)
    odb = openOdb(odb_path, readOnly=True)

    print("Exporting mesh.json ...")
    export_mesh(odb, out_dir)

    print("Exporting steps.json ...")
    export_steps(odb, step_filter, out_dir)

    print("Exporting nt11.jsonl ...")
    export_nt11(odb, step_filter, out_dir)

    odb.close()
    print("✅ Done. Files written to:", out_dir)

if __name__ == "__main__":
    main()
