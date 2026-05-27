use std::fmt;

pub struct Server {
    host: String,
    port: u16,
}

pub enum Status {
    Active,
    Inactive,
}

pub trait Handler {
    fn handle(&self, req: &str) -> String;
}

pub fn new_server(host: &str) -> Server {
    Server {
        host: host.to_string(),
        port: 8080,
    }
}

impl Server {
    pub fn new() -> Self {
        Server {
            host: String::from("localhost"),
            port: 8080,
        }
    }

    pub fn start(&self) {
        println!("Starting on {}:{}", self.host, self.port);
        self.listen();
    }

    fn listen(&self) {
        println!("listening");
    }
}

impl Handler for Server {
    fn handle(&self, req: &str) -> String {
        format!("OK: {}", req)
    }
}

impl fmt::Display for Server {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.host, self.port)
    }
}
