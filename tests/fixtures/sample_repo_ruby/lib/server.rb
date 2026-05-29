require 'json'
require_relative './utils'

class Server
  def initialize(host, port)
    @host = host
    @port = port
  end

  def start
    puts "Starting on #{@host}:#{@port}"
    self.listen
  end

  def self.create(host)
    new(host, 8080)
  end

  private

  def listen
    puts 'listening'
  end
end

module Handler
  def handle(req)
    greet(req)
    "OK: #{req}"
  end
end

def greet(name)
  "Hello, #{name}!"
end
