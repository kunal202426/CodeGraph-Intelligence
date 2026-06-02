#include <string>
#include "server.h"

class Server {
public:
    std::string host;
    int port;

    Server(std::string h, int p) : host(h), port(p) {}

    void start() {
        printf("Starting\n");
        this->listen();
    }

    static Server *create(const std::string &host) {
        return new Server(host, 8080);
    }

private:
    void listen() {
        printf("listening\n");
    }
};

class Handler {
public:
    virtual std::string handle(const std::string &req) = 0;
};

void greet(const std::string &name) {
    printf("Hello!\n");
}
