# -*- coding: utf-8 -*-
# import_odb_nt11_2019.py
#
# Rebuild an Abaqus 2019 ODB from:
#   - mesh.json   (instances -> nodes/elements)
#   - steps.json  (step + frame metadata)
#   - nt11.jsonl  (streamed NT11 buckets per step/frame/instance/position[/sectionPoint])
#
# IMPORTANT (Abaqus 2019 sequence):
#  1) Create ODB + Mesh (Parts/Nodes/Elements/Instances)
#  2) SAVE & CLOSE  <-- commits geometry repository
#  3) Reopen ODB (readOnly=False)
#  4) Create Steps/Frames and write FieldOutput data
#
# Usage (from an Abaqus 2019 command prompt):
#   "C:\SIMULIA\Commands\abq2019hf6.bat" python import_odb_nt11_2019.py \
#       --mesh mesh.json --steps steps.json --nt11 nt11.jsonl --out Thermal_Combined_2019.odb

from abaqusConstants import *
from odbAccess import Odb, openOdb
from odbMaterial import *
from odbSection import *
import os, sys, json

# ---------------- Helpers (Py2.7-safe) ----------------
try:
    unicode  # Py2
except NameError:
    unicode = str  # Py3 fallback, not used in 2019, but keeps function portable

def to_str(s):
    """Force unicode -> byte str for Abaqus 2019 string APIs."""
    if isinstance(s, unicode):
        try:
            return s.encode('utf-8')
        except:
            return str(s)
    return str(s)

def pos_from_name(name):
    name = to_str(name)
    if name == 'NODAL': return NODAL
    if name == 'ELEMENT_NODAL': return ELEMENT_NODAL
    if name == 'INTEGRATION_POINT': return INTEGRATION_POINT
    if name == 'WHOLE_ELEMENT': return WHOLE_ELEMENT
    return NODAL

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def ensure_tuple_seq(seq):
    # list-of-lists -> tuple-of-tuples for addData(...)
    return tuple(tuple(x) if isinstance(x, (list, tuple)) else (x,) for x in seq)

def ensure_tuple(seq):
    return tuple(seq if isinstance(seq, (list, tuple)) else [seq])

def stream_jsonl(path):
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)

# ---------------- CLI ----------------
def parse_args(argv):
    mesh_path = steps_path = nt11_path = out_odb = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--mesh':
            i += 1; mesh_path = argv[i]
        elif a == '--steps':
            i += 1; steps_path = argv[i]
        elif a == '--nt11':
            i += 1; nt11_path = argv[i]
        elif a in ('--out', '--odb'):
            i += 1; out_odb = argv[i]
        i += 1
    if not (mesh_path and steps_path and nt11_path and out_odb):
        sys.stderr.write("Usage: abaqus python import_odb_nt11_2019.py --mesh mesh.json --steps steps.json --nt11 nt11.jsonl --out out.odb\n")
        sys.exit(2)
    return mesh_path, steps_path, nt11_path, out_odb

# ---------------- Main ----------------
def main():
    mesh_path, steps_path, nt11_path, out_odb = parse_args(sys.argv[1:])
    out_odb = to_str(out_odb)

    # ========== Stage 1: Geometry (mesh) ==========
    print(("Creating new 2019 ODB:", out_odb))
    odb = Odb(name=to_str('derivedNT11'),
              analysisTitle=to_str('Imported from JSON(L)'),
              description=to_str('Mesh + NT11 reconstructed from export'),
              path=out_odb)

    ra = odb.rootAssembly

    print(("Rebuilding mesh from:", to_str(mesh_path)))
    mesh = load_json(mesh_path)

    # Build parts & instances (part-per-instance is simplest & robust)
    for iname_u, inst_rec in mesh['instances'].items():
        iname = to_str(iname_u)
        part_name = to_str('PART_FROM_' + iname)

        # Create part as 3D deformable (works for padded 2D coords too)
        p = odb.Part(name=part_name, embeddedSpace=THREE_D, type=DEFORMABLE_BODY)

        # Nodes
        nodes = inst_rec.get('nodes', [])
        if nodes:
            nodeData = []
            for rec in nodes:  # [label, x, y, z]
                nodeData.append((int(rec[0]), float(rec[1]), float(rec[2]), float(rec[3])))
            p.addNodes(nodeData=tuple(nodeData))

        # Elements (group by type)
        elements = inst_rec.get('elements', [])
        if elements:
            groups = {}
            for e in elements:
                ety = to_str(e['type'])
                groups.setdefault(ety, []).append(
                    (int(e['label']),) + tuple(int(n) for n in e['connectivity'])
                )
            for ety, eData in groups.items():
                p.addElements(elementData=tuple(eData), type=ety)

        # Instance AFTER nodes/elements defined on the part
        ra.Instance(name=iname, object=p)

    # --- critical for 2019: commit geometry before writing results ---
    print("Committing geometry (save & close) ...")
    odb.save()
    odb.close()

    # ========== Stage 2: Results (steps/frames/fields) ==========
    print("Reopening ODB for results write ...")
    odb = openOdb(path=out_odb, readOnly=False)
    ra = odb.rootAssembly

    # Rebuild an instance map from the reopened ODB
    inst_map = {}
    for k, v in ra.instances.items():
        inst_map[to_str(k)] = v

    # Generic section category for integration-point data
    sCat = odb.SectionCategory(name=to_str('GEN_SEC_CAT'),
                               description=to_str('Generic section category for imported IP data'))
    sp_pool = {}  # (number, description) -> SectionPoint

    def get_or_make_sp(sp_dict):
        if sp_dict is None:
            return None
        num = sp_dict.get('number', None)
        desc = sp_dict.get('description', '')
        key = (int(num) if num is not None else 1, to_str(desc))
        if key in sp_pool:
            return sp_pool[key]
        sp_obj = sCat.SectionPoint(number=key[0], description=key[1])
        sp_pool[key] = sp_obj
        return sp_obj

    # Steps & frames
    print(("Creating steps/frames from:", to_str(steps_path)))
    steps_json = load_json(steps_path)
    step_objs = {}
    frame_objs = {}

    for s in steps_json.get('steps', []):
        sname = to_str(s['name'])
        domain = TIME  # exporter provided TIME domain
        # Choose timePeriod >= last frame value
        tp = 0.0
        if s.get('frames'):
            try:
                tp = float(s['frames'][-1]['value'])
            except:
                tp = 0.0
        stp = odb.Step(name=sname, description=to_str('Imported step'), domain=domain, timePeriod=max(tp, 0.0))
        step_objs[sname] = stp

        # Pre-create frames
        for fr in s.get('frames', []):
            fidx = int(fr['index'])
            fval = float(fr.get('value', 0.0))
            fdesc = to_str(fr.get('description', ''))
            fobj = stp.Frame(incrementNumber=fidx, frameValue=fval, description=fdesc)
            frame_objs[(sname, fidx)] = fobj

    # Stream NT11 and write
    print(("Writing NT11 from:", to_str(nt11_path)))
    nt11_cache = {}  # (step, frame) -> FieldOutput

    count = 0
    for rec in stream_jsonl(nt11_path):
        count += 1
        sname = to_str(rec['step'])
        fidx  = int(rec['frame_index'])
        fval_check = float(rec['frame_value'])
        iname = to_str(rec['instance'])
        pos   = pos_from_name(rec['position'])
        sp    = rec.get('section_point', None)
        labels = [int(x) for x in rec['labels']]
        values = rec['values']  # list of scalar lists e.g. [[T], [T], ...]

        # Get or create frame (if not in steps.json for any reason)
        fkey = (sname, fidx)
        if fkey not in frame_objs:
            stp = step_objs[sname]
            fobj = stp.Frame(incrementNumber=fidx, frameValue=fval_check, description=to_str(''))
            frame_objs[fkey] = fobj
        fobj = frame_objs[fkey]

        # FieldOutput per frame
        if fkey in nt11_cache:
            fo = nt11_cache[fkey]
        else:
            fo = fobj.FieldOutput(name=to_str('NT11'),
                                  description=to_str('Imported temperature'),
                                  type=SCALAR, validInvariants=())
            nt11_cache[fkey] = fo

        # SectionPoint object if needed
        sp_obj = get_or_make_sp(sp)

        # Prepare payload
        labels_t = ensure_tuple(labels)
        data_t   = ensure_tuple_seq(values)

        if iname not in inst_map:
            raise RuntimeError("Instance '%s' not found when writing NT11." % iname)
        inst = inst_map[iname]

        # Write
        if sp_obj is not None and pos == INTEGRATION_POINT:
            fo.addData(position=pos, instance=inst, labels=labels_t, data=data_t, sectionPoint=sp_obj)
        else:
            fo.addData(position=pos, instance=inst, labels=labels_t, data=data_t)

    print(("NT11 buckets written:", count))

    # Finalize
    odb.save()
    odb.close()
    print(("✅ Done. Wrote:", out_odb))

if __name__ == "__main__":
    main()
