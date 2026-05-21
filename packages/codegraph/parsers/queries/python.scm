; Tree-sitter S-expression patterns for Python entity capture.
; The actual parser (python.py) uses a recursive walk instead of running these
; queries, because tracking the parent-class scope chain is cleaner that way.
; Kept here as reference for the node types we care about.

; Entity-defining nodes (T1.3)
(class_definition name: (identifier) @class.name) @class.def
(function_definition name: (identifier) @function.name) @function.def
(decorated_definition definition: (_ name: (identifier) @decorated.name)) @decorated.def

; Import statements (T2.1) — emit "imports" edges with provisional dst_ids
; that the symbol resolver (T2.2) closes against real entity_ids.
(import_statement name: (_) @import.name) @import.stmt
(import_from_statement module_name: (_) @import.module name: (_) @import.from_name) @import.from
(import_from_statement (wildcard_import) @import.wildcard) @import.from.wildcard
