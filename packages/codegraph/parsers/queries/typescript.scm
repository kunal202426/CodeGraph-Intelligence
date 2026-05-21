; Tree-sitter S-expression patterns for TypeScript/TSX/JS/JSX entity capture.
; The actual parser (typescript.py) uses a recursive walk; these patterns are
; reference documentation of the node types we look at.

; Entities (T2.4)
(function_declaration name: (identifier) @function.name) @function.def
(class_declaration name: (type_identifier) @class.name) @class.def
(interface_declaration name: (type_identifier) @interface.name) @interface.def
(method_definition name: (property_identifier) @method.name) @method.def
(variable_declarator name: (identifier) @const_arrow.name value: (arrow_function)) @const_arrow.def

; Export wrappers
(export_statement declaration: (_) @exported.decl) @exported.stmt

; Imports (T2.5) — emit "imports" edges resolved against the file system in
; the symbol resolver (./relative paths + .ts/.tsx/.js/.jsx + index.* lookup).
(import_statement source: (string) @import.specifier) @import.stmt
(import_specifier name: (identifier) @import.named_name) @import.named
(namespace_import (identifier) @import.namespace_alias) @import.namespace

