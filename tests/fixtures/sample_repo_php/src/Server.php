<?php

namespace App\Server;

use App\Http\Request;
use App\Http\{Response, Middleware};

require_once 'vendor/autoload.php';

class Server {
    private string $host;
    private int $port;

    public function __construct(string $host, int $port) {
        $this->host = $host;
        $this->port = $port;
    }

    public function start(): void {
        echo "Starting on {$this->host}:{$this->port}";
        $this->listen();
    }

    private function listen(): void {
        echo "listening";
    }

    public static function create(string $host): self {
        return new self($host, 8080);
    }
}

interface Handler {
    public function handle(string $req): string;
}

trait Logging {
    public function log(string $msg): void {
        echo $msg;
    }
}

function greet(string $name): string {
    return "Hello, $name!";
}
