# AriaOps MCP — Auth Test Harness

This is a **testing frontend** for the AriaOps MCP server. It demonstrates that
end-user authentication flows correctly through to the MCP server's role-based
authorization.

- **Password login** → forwarded to the MCP server as HTTP **Basic** auth → the
  server performs the **LDAP/AD** bind and maps your groups to a role.
- **OAuth login** → your IdP **access token** is forwarded as a **Bearer** token
  → the server validates it and reads your role claims.

After login you'll see your **server-resolved role** and the **instances** you're
allowed to reach. Authorization is enforced entirely by the MCP server — this UI
only forwards your credential.

> ⚠️ This is a local test tool. Run it over HTTPS in any shared environment; it
> forwards live credentials to the MCP server on every tool call.
