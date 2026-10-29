// A valid Mini-Java program
class Main {
    static int fib(int n) {
        if (n < 2) {
            return n;
        }
        return fib(n - 1) + fib(n - 2);
    }

    public static void main(String[] args) {
        int total = 0;
        int i = 0;
        while (i < 10) {
            total = total + fib(i);
            i = i + 1;
        }
        String msg = "total: " + total;
        System.out.println(msg);
    }
}
