#include <stdio.h>
#include "server.h"

typedef struct {
    char *host;
    int port;
} Server;

struct Config {
    int timeout;
};

Server *server_new(const char *host, int port) {
    Server *s = malloc(sizeof(Server));
    s->host = host;
    s->port = port;
    return s;
}

void server_start(Server *s) {
    printf("Starting %s:%d\n", s->host, s->port);
    server_listen(s);
}

static void server_listen(Server *s) {
    printf("listening\n");
}

int main(int argc, char *argv[]) {
    Server *s = server_new("localhost", 8080);
    server_start(s);
    return 0;
}
