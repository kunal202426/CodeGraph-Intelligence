; Tree-sitter S-expression patterns for Python entity capture.
; The actual parser (python.py) uses a recursive walk instead of running these
; queries, because tracking the parent-class scope chain is cleaner that way.
; Kept here as reference for the node types we care about.

(class_definition name: (identifier) @class.name) @class.def
(function_definition name: (identifier) @function.name) @function.def
(decorated_definition definition: (_ name: (identifier) @decorated.name)) @decorated.def
