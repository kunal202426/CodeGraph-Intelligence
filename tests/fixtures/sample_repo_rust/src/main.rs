use std::io;
use crate::server::Server;

fn main() {
    let s = Server::new();
    s.start();
    greet("world");
}

fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}
