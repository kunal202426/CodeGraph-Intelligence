package com.example.server;

import java.util.List;
import java.io.*;

public class Server {
    private String host;
    private int port;

    public Server(String host, int port) {
        this.host = host;
        this.port = port;
    }

    public void start() {
        System.out.println("Starting on " + host + ":" + port);
        this.listen();
    }

    private void listen() {
        System.out.println("listening");
    }

    public static Server create(String host) {
        return new Server(host, 8080);
    }
}

enum Status {
    ACTIVE, INACTIVE
}

interface Handler {
    String handle(String req);
}
