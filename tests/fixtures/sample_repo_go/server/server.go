package server

import "fmt"

type Server struct {
	host string
	port int
}

type Handler interface {
	Handle(req string) string
}

func New() *Server {
	return &Server{host: "localhost", port: 8080}
}

func (s *Server) Start() {
	fmt.Printf("Starting on %s:%d\n", s.host, s.port)
	s.listen()
}

func (s *Server) listen() {
	fmt.Println("listening")
}
