; Ruby tree-sitter query reference (documentation only — parser uses the
; Python tree-sitter API directly rather than the query engine).
;
; Top-level type declarations
(class name: (constant) @class.name) @class
(module name: (constant) @module.name) @module
;
; Top-level function
(method name: (identifier) @function.name) @function
;
; Methods and singleton methods inside a class / module body
(method name: (identifier) @method.name) @method
(singleton_method name: (identifier) @singleton.name) @singleton
;
; Imports
(call method: (identifier) @require (#match? @require "^require")) @import
;
; Call edges
(call method: (identifier) @call.name) @call
