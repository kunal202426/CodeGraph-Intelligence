; Java tree-sitter query reference (documentation only — parser uses the
; Python tree-sitter API directly rather than the query engine).
;
; Top-level declarations
(class_declaration name: (identifier) @class.name) @class
(interface_declaration name: (identifier) @interface.name) @interface
(enum_declaration name: (identifier) @enum.name) @enum
;
; Methods and constructors inside a class / interface body
(method_declaration name: (identifier) @method.name) @method
(constructor_declaration name: (identifier) @constructor.name) @constructor
;
; Imports
(import_declaration) @import
;
; Call edges
(method_invocation name: (identifier) @call.name) @call
