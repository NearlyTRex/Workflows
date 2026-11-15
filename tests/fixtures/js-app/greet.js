// The fixture's code: the tests must run every statement, and both arms of the ?:
function greet(name) {
  return name ? `Hello, ${name}!` : "Hello!";
}

module.exports = { greet };
