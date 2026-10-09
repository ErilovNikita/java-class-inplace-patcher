package fixture;

public class Sample {
    public boolean enabled() {
        return false;
    }

    public int identity(int value) {
        return value;
    }

    public int repeated() {
        int first = 0;
        int second = 0;
        return first + second;
    }

    public String label() {
        return "license";
    }
}
