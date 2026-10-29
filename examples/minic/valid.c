// A valid Mini-C program
int fib(int n) {
    if (n < 2) {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

int main() {
    int total = 0;
    int i = 0;
    while (i < 10) {
        total = total + fib(i);
        i = i + 1;
    }
    if (total > 50) {
        printf("big");
    }
    return total;
}
