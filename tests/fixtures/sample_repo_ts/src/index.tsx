/** TSX entry — confirms JSX parses through the tsx grammar. */

import { authenticate, LoginForm } from "./auth/login";

export function App() {
  const form = new LoginForm("smoke@example.com");
  if (form.validate() && authenticate(form.email, "abc")) {
    return <div>welcome</div>;
  }
  return <div>denied</div>;
}

export default App;
