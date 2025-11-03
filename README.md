# Abaqus ODB NT11 Version Converter

This tool allows you to convert Abaqus ODB files between different versions by exporting and importing NT11 field output data. It's particularly useful when you need to work with thermal results (NT11) across different Abaqus versions.

## Components

- `export_odb_nt11.py`: Exports NT11 data from an ODB file into version-neutral JSON format
- `import_odb_nt11_2019.py`: Imports the JSON data into a new Abaqus 2019 ODB file

## Export Process

The export script creates three files:
- `mesh.json`: Contains mesh information per instance (nodes, elements, element types)
- `steps.json`: Contains step and frame information (time, description)
- `nt11.jsonl`: Contains NT11 values in JSON Lines format (one JSON object per data bucket)

### Usage - Export

```bash
abaqus python export_odb_nt11.py --odb "path/to/file.odb" --out export_dir [--steps Thermal_Step,OtherStep]
```

Options:
- `--odb` (or `-i`): Path to input ODB file
- `--out` (or `-o`): Output directory for JSON files
- `--steps` (or `-s`): Optional comma-separated list of step names to export (defaults to all steps)

## Import Process

The import script rebuilds an Abaqus 2019 ODB file from the exported JSON data. 

### Usage - Import

```bash
abaqus2019 python import_odb_nt11_2019.py --mesh mesh.json --steps steps.json --nt11 nt11.jsonl --out Thermal_Combined_2019.odb
```

Options:
- `--mesh`: Path to exported mesh.json
- `--steps`: Path to exported steps.json
- `--nt11`: Path to exported nt11.jsonl
- `--out` (or `--odb`): Path for the new ODB file

## Data Format

### NT11 Data Format (nt11.jsonl)
Each line in nt11.jsonl contains a JSON object with:
```json
{
  "step": "Thermal_Step",
  "frame_index": 3,
  "frame_value": 12.345,
  "instance": "PART-1-1",
  "position": "NODAL" | "ELEMENT_NODAL" | "INTEGRATION_POINT" | "WHOLE_ELEMENT",
  "section_point": {"number":5, "description":"Top"} | null,
  "labels": [...],  // node or element labels
  "values": [[v], [v], ...]  // NT11 values as 1-tuples
}
```

## Important Notes

1. The import process in Abaqus 2019 follows a specific sequence:
   - Create ODB + Mesh (Parts/Nodes/Elements/Instances)
   - Save and close to commit geometry repository
   - Reopen ODB
   - Create Steps/Frames and write FieldOutput data

2. All strings are handled with UTF-8 encoding for compatibility.

3. The tool supports different position types:
   - NODAL
   - ELEMENT_NODAL
   - INTEGRATION_POINT
   - WHOLE_ELEMENT

## Requirements

- Abaqus with Python support
- Source ODB with NT11 field output results
- For import: Abaqus 2019 (other versions may require modifications to import script)

## License

MIT License
