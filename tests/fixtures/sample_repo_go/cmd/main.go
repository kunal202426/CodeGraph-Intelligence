package main

import (
	"fmt"
	"sample/server"
)

func main() {
	s := server.New()
	s.Start()
	greet("world")
}

func greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}
